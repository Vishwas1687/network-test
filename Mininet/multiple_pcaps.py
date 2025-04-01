import random
import netaddr
import threading
from mininet.net import Mininet
from mininet.topo import Topo
from mininet.log import setLogLevel, info
from mininet.cli import CLI
from mininet.link import TCLink
from mininet.node import OVSKernelSwitch, RemoteController
import os
import scapy.all as scapy
import tempfile
import json
import time

global_ip_map = {}

def is_public_ip(ip):
    ip_obj = netaddr.IPAddress(ip)
    return not (ip_obj.is_private() or ip_obj.is_multicast() or ip_obj.is_reserved() or ip_obj.is_loopback())

def extract_public_ips(pcap_file):
    packets = scapy.rdpcap(pcap_file)
    public_ips = set()
    for packet in packets:
        if scapy.IP in packet:
            src_ip = packet[scapy.IP].src
            public_ips.add(src_ip)
    return list(public_ips)

def generate_ip_mapping(public_ips):
    return {public_ip: f'10.0.0.{i+1}' for i, public_ip in enumerate(public_ips)}

class RandomTopo(Topo):
    def build(self, host_count):
        s1 = self.addSwitch("s1")
        s2 = self.addSwitch("s2")
        for i in range(1, int(host_count/2)):
            host = self.addHost(f'h{i}')
            self.addLink(host, s1, bw=10, delay='5ms')
        for i in range(int(host_count/2), host_count + 1):
            host = self.addHost(f'h{i}')
            self.addLink(host, s2, bw=10, delay='5ms')
        self.addLink(s1, s2,  bw=10, delay='5ms')

def get_host_from_ip(net, src_ip):
    private_ip = global_ip_map.get(src_ip)
    if private_ip:
        for host in net.hosts:
            if host.IP() == private_ip:
                return host
    return None

def replay_packets(net, pcap_file):
    packets = scapy.rdpcap(pcap_file)
    packets_by_src = {}
    for packet in packets:
        if scapy.IP in packet:
            src_ip = packet[scapy.IP].src
            packet[scapy.IP].version = 4
            if src_ip not in packets_by_src:
                packets_by_src[src_ip] = []
            packets_by_src[src_ip].append(packet)

    for src_ip, ip_packets in packets_by_src.items():
        host = get_host_from_ip(net, src_ip)
        if not host:
            continue
        info(f"Replaying {len(ip_packets)} packets from {global_ip_map.get(src_ip)} on {host.name}...\n")

        modified_ip_packets = []
        for pkt in ip_packets:
            if scapy.IP in pkt:
                pkt[scapy.IP].src = global_ip_map.get(pkt[scapy.IP].src, pkt[scapy.IP].src)
                pkt[scapy.IP].dst = global_ip_map.get(pkt[scapy.IP].dst, pkt[scapy.IP].dst)
                del pkt[scapy.IP].chksum  # Recalculate checksum automatically
                modified_ip_packets.append(pkt)

        # Save modified packets to a temporary PCAP file
        temp_pcap = tempfile.NamedTemporaryFile(suffix='.pcap', delete=False)
        scapy.wrpcap(temp_pcap.name, modified_ip_packets)

        # Save IP mappings to a temporary JSON file for the script to access
        temp_json = tempfile.NamedTemporaryFile(suffix='.json', delete=False)
        json.dump(global_ip_map, open(temp_json.name, "w"))

        # Create a temporary script to replay packets
        temp_script = tempfile.NamedTemporaryFile(suffix='.py', delete=False)
        script_content = f"""
import os
import scapy.all as scapy
import json

# Load global IP map from JSON file
with open("{temp_json.name}", "r") as f:
    global_ip_map = json.load(f)

# Flag to ensure ping runs only once
ping_executed = False

def send_packet(packet, iface):
    global ping_executed
    try:
        print(f"Sending packet with source IP: {{packet[scapy.IP].src}} and destination IP: {{packet[scapy.IP].dst}}")
        scapy.sendp(packet, iface=iface, verbose=0)

        # Run ping only when the script starts executing, not for each packet
        if not ping_executed:
            src_ip = packet[scapy.IP].src
            for dst_ip in global_ip_map.values():
                if dst_ip != src_ip:
                    os.system(f"ping -c 3 {{dst_ip}}")
            ping_executed = True

    except Exception as e:
        print(f'Error in sending packet: {{e}}')

print("Starting packet replay...")
packets = scapy.rdpcap('{temp_pcap.name}')
print(f"Total packets to replay: {{len(packets)}}")
for pkt in packets:
    send_packet(pkt, '{host.name}-eth0')
print("Packet replay completed.")
"""
        temp_script.write(script_content.encode())
        temp_script.close()

        # Run the generated script on the Mininet host
        output = host.cmd(f'python3 {temp_script.name}')
        print(output)

        # Cleanup temporary files
        os.remove(temp_script.name)
        os.remove(temp_pcap.name)
        os.remove(temp_json.name)

        time.sleep(0.5)

def run_topology():
    global global_ip_map
    setLogLevel('info')

    # Extract public IPs from PCAP files
    pcap_files = [f for f in os.listdir('.') if f.startswith('traffic') and f.endswith('.pcap')]
    all_public_ips = set()
    for pcap_file in pcap_files:
        all_public_ips.update(extract_public_ips(pcap_file))
    
    if not all_public_ips:
        info("No valid public IPs found in the PCAP files.\n")
        return

    global_ip_map.update(generate_ip_mapping(list(all_public_ips)))

    # Create Mininet topology
    topo = RandomTopo(len(global_ip_map))
    net = Mininet(topo=topo, controller=lambda name: RemoteController(name, ip='127.0.0.1', port=6633), link=TCLink)
    net.start()

    info(f"Mapped {len(global_ip_map)} public IPs to private subnet:\n{global_ip_map}\n")

    # Start replaying packets in separate threads
    threads = []
    for pcap_file in pcap_files:
        thread = threading.Thread(target=replay_packets, args=(net, pcap_file))
        thread.start()
        threads.append(thread)

    for thread in threads:
        thread.join()

    # Check OVS flow tables
    info("\nChecking OVS Flow Table:\n")
    os.system("sudo ovs-ofctl dump-flows s1")

    info("\nChecking OVS Port Table:\n")
    os.system("sudo ovs-ofctl dump-ports s1")

    # Start Mininet CLI
    CLI(net)
    net.stop()

if __name__ == "__main__":
    run_topology()
