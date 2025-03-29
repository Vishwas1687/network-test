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
import time
import signal
import sys

global_ip_map = {}

def signal_handler(sig, frame):
    print("Ctrl+C detected, cleaning up...")
    os.system("sudo mn -c")
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)

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
        switches = [self.addSwitch(f's{i+1}', cls=OVSKernelSwitch) for i in range(10)]
        for i in range(1, host_count + 1):
            host = self.addHost(f'h{i}', ip=f'10.0.0.{i}', mac=f'00:00:00:00:00:{i:02x}')
            self.addLink(host, random.choice(switches))
        for _ in range(15):
            s1, s2 = random.sample(switches, 2)
            self.addLink(s1, s2)

def verify_controller_connection():
    """Check if the OpenFlow controller is reachable."""
    controller_ip = '127.0.0.1'
    controller_port = 6633

    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(3)
    try:
        s.connect((controller_ip, controller_port))
        s.close()
        return True
    except:
        return False

def enable_ip_forwarding(net):
    """Enable IP forwarding on all hosts"""
    for host in net.hosts:
        host.cmd("sysctl -w net.ipv4.ip_forward=1")
        host.cmd("sysctl -w net.ipv4.conf.all.rp_filter=0")

def set_arp_entries(net):
    """Pre-populate ARP entries to prevent packet loss"""
    for h1 in net.hosts:
        for h2 in net.hosts:
            if h1 != h2:
                h1.cmd(f"ping -c 1 {h2.IP()}")

def configure_openflow_rules(net):
    """Add OpenFlow rules to allow all traffic"""
    for switch in net.switches:
        switch.cmd("ovs-ofctl add-flow", switch.name, "priority=65535,actions=NORMAL")

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
                pkt[scapy.IP].src = global_ip_map.get(pkt[scapy.IP].src)
                pkt[scapy.IP].dst = global_ip_map.get(pkt[scapy.IP].dst)
                del pkt[scapy.IP].chksum
                modified_ip_packets.append(pkt)
        temp_pcap = tempfile.NamedTemporaryFile(suffix='.pcap', delete=False)
        scapy.wrpcap(temp_pcap.name, modified_ip_packets)
        temp_script = tempfile.NamedTemporaryFile(suffix='.py', delete=False)
        script_content = f"""
import os
import scapy.all as scapy

def send_packet(packet, iface):
    try:
        print(f"Sending packet with source IP: {{packet[scapy.IP].src}} and destination IP: {{packet[scapy.IP].dst}}");
        scapy.sendp(packet, iface=iface, verbose=0)
        print(f"Packet sent successfully on interface {{iface}}")
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
        output = host.cmd(f'python3 {temp_script.name}')
        print(output)
        os.remove(temp_script.name)
        os.remove(temp_pcap.name)
        time.sleep(0.5)

def run_topology():
    global global_ip_map
    setLogLevel('info')

    if not verify_controller_connection():
        info("ERROR: Cannot connect to the Ryu controller at 127.0.0.1:6633\n")
        info("Ensure the controller is running before executing this script\n")
        return

    pcap_files = [f for f in os.listdir('.') if f.startswith('traffic') and f.endswith('.pcap')]
    all_public_ips = set()
    for pcap_file in pcap_files:
        all_public_ips.update(extract_public_ips(pcap_file))
    if not all_public_ips:
        info("No valid public IPs found in the PCAP files.\n")
        return

    global_ip_map.update(generate_ip_mapping(list(all_public_ips)))
    topo = RandomTopo(len(global_ip_map))
    net = Mininet(
        topo=topo, 
        controller=lambda name: RemoteController(name, ip='127.0.0.1', port=6633), 
        link=TCLink
    )
    net.start()
    
    enable_ip_forwarding(net)
    set_arp_entries(net)
    configure_openflow_rules(net)

    info(f"Mapped {len(global_ip_map)} public IPs to private subnet:\n{global_ip_map}\n")

    threads = []
    for pcap_file in pcap_files:
        thread = threading.Thread(target=replay_packets, args=(net, pcap_file))
        thread.start()
        threads.append(thread)
    for thread in threads:
        thread.join()

    info("\nChecking OVS Flow Table:\n")
    os.system("sudo ovs-ofctl dump-flows s1")
    info("\nChecking OVS Port Table:\n")
    os.system("sudo ovs-ofctl dump-ports s1")
    CLI(net)
    net.stop()
    os.system("sudo mn -c")

if __name__ == "__main__":
    run_topology()

