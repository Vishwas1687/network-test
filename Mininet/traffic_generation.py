from mininet.net import Mininet
from mininet.topo import Topo
from mininet.link import TCLink
from mininet.node import OVSKernelSwitch, RemoteController
from mininet.log import setLogLevel
from mininet.cli import CLI
import threading
import time
import random
import os

# Custom topology with 5 switches and 30 hosts
class CustomTopo(Topo):
    def __init__(self):
        Topo.__init__(self)

        switches = []
        hosts = []

        # Add 5 switches
        for i in range(1, 6):
            switch = self.addSwitch(f's{i}')
            switches.append(switch)

        # Add 30 hosts and connect them to switches in a round-robin fashion
        for i in range(1, 31):
            host = self.addHost(f'h{i}')
            hosts.append(host)
            self.addLink(host, switches[i % 5])  # Distribute hosts across switches

        # Interconnect switches
        for i in range(4):
            self.addLink(switches[i], switches[i + 1])

# Function to start iperf (genuine high traffic)
def run_iperf(net, sender, receiver, duration=60):
    h1 = net.get(sender)
    h2 = net.get(receiver)
    
    print(f"Starting iPerf Server on {receiver}")
    h2.cmd('pkill -f iperf')  # Kill any existing iperf processes
    h2.cmd('iperf -s &')
    time.sleep(2)

    print(f"Running iPerf Client from {sender} to {receiver}")
    # Run in background and for the full duration
    h1.cmd(f'iperf -c {h2.IP()} -t {duration} -i 1 -b 10M &')
    
    # Keep the thread alive for the duration
    time.sleep(duration)
    print(f"iPerf traffic from {sender} to {receiver} completed")

# Function to start SYN flood attack using hping3
def run_hping3_syn_flood(net, attacker, target, duration=60):
    h_attacker = net.get(attacker)
    h_target = net.get(target)

    print(f"Starting SYN Flood attack from {attacker} to {target}")
    h_attacker.cmd(f"hping3 -S --faster -p 80 {h_target.IP()} &")
    time.sleep(duration)  # Run for the specified duration
    h_attacker.cmd("pkill -f hping3")
    print(f"Stopped SYN Flood attack from {attacker}")

# Function to start UDP flood attack using hping3
def run_hping3_udp_flood(net, attacker, target, duration=60):
    h_attacker = net.get(attacker)
    h_target = net.get(target)

    print(f"Starting UDP Flood attack from {attacker} to {target}")
    h_attacker.cmd(f"hping3 --udp --faster -p 53 {h_target.IP()} &")
    time.sleep(duration)  # Run for the specified duration
    h_attacker.cmd("pkill -f hping3")
    print(f"Stopped UDP Flood attack from {attacker}")

# Function to run a port scan using hping3
def run_hping3_scan(net, scanner, target, duration=60):
    h_scanner = net.get(scanner)
    h_target = net.get(target)

    print(f"Starting port scan on {target} from {scanner}")
    # Run the scan in the background for the scan to continue during the simulation
    h_scanner.cmd(f"hping3 -S {h_target.IP()} -p 1-1000 --scan &")
    time.sleep(duration)  # Keep the thread alive for the duration
    h_scanner.cmd("pkill -f hping3")
    print(f"Finished port scan on {target}")

def generate_normal_traffic(net, duration=180):
    """
    Generates substantial background traffic across the network to simulate realistic internet activity.
    """
    hosts = [net.get(f'h{i}') for i in range(1, 31)]
    
    print("Starting enhanced normal background traffic...")
    start_time = time.time()
    end_time = start_time + duration
    
    # Keep generating additional dynamic traffic
    while time.time() < end_time:
        remaining = end_time - time.time()
        if remaining <= 0:
            break
            
        for i in range(5):
            server_host = random.choice(hosts)
            client_host = random.choice([h for h in hosts if h != server_host])
        
            # Start iperf server
            server_host.cmd('iperf -s > /dev/null 2>&1 &')
        
            # Start continuous client with varying bandwidths (all low but different)
            bandwidth = random.randint(6, 12)  # 6K to 12K bps
            duration2 = random.randint(10,30)
            client_host.cmd(f'iperf -c {server_host.IP()} -l 100 -t {duration2} -b {bandwidth}K > /dev/null 2>&1 &')
            
        # Wait longer between iterations to reduce control overhead
        time.sleep(5)
    
    print("Normal background traffic completed.")
    
    # Cleanup the persistent sessions
    for host in hosts:
        host.cmd("pkill -f iperf")
        host.cmd("pkill -f nmap")
        host.cmd("pkill -f host")

# Main function to control network and start traffic
def start_network():
    topo = CustomTopo()
    net = Mininet(topo=topo, controller=lambda name: RemoteController(name, ip='127.0.0.1', port=6633), link=TCLink)
    net.start()

    # Choose hosts dynamically
    sender, receiver = "h1", "h2"
    syn_attacker, syn_target = "h3", "h4"
    udp_attacker, udp_target = "h5", "h6"
    scanner, scan_target = "h7", "h8"
    slowloris_attacker, slowloris_target = "h9", "h10"

    # Wait for controller to establish
    print("Waiting for the controller to establish connection...")
    time.sleep(5)

    # Traffic simulation duration in seconds
    simulation_duration = 180
    
    # Create threads for all types of traffic
    threads = [
        #threading.Thread(target=run_iperf, args=(net, sender, receiver, simulation_duration)),
        threading.Thread(target=generate_normal_traffic, args=(net, simulation_duration)),
        #threading.Thread(target=run_hping3_syn_flood, args=(net, syn_attacker, syn_target, simulation_duration)),
        #threading.Thread(target=run_hping3_udp_flood, args=(net, udp_attacker, udp_target, simulation_duration)),
        #threading.Thread(target=run_hping3_scan, args=(net, scanner, scan_target, simulation_duration)),
    ]
    
    # Start all threads
    print("\n=== Starting all traffic generators simultaneously ===")
    for thread in threads:
        thread.daemon = True  # Make threads daemon so they exit when main thread exits
        thread.start()
    
    # Wait some time before opening CLI to let traffic run
    print(f"\nAll traffic generators started. Running for {simulation_duration} seconds...")
    time.sleep(simulation_duration)
    
    # Check final packet counts
    for switch_name in ['s1', 's2', 's3', 's4', 's5']:
        switch = net.get(switch_name)
        print(f"\n=== Final packet counters for {switch_name} ===")
        result = switch.cmd(f'ovs-ofctl dump-flows {switch_name}')
        print(result)
    
    # Open CLI for manual testing
    print("\nAll traffic generation completed. Opening CLI for manual testing...")
    CLI(net)
    
    # Cleanup
    print("Cleaning up...")
    for host in net.hosts:
        host.cmd("pkill -f iperf")
        host.cmd("pkill -f hping3")
    
    net.stop()

if __name__ == '__main__':
    setLogLevel('info')
    start_network()
