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
def run_iperf(net, sender, receiver, port, duration):
    h1 = net.get(sender)
    h2 = net.get(receiver)
    
    print(f"Starting iPerf Server on {receiver}")
    h2.cmd('pkill -f iperf')  # Kill any existing iperf processes
    h2.cmd(f'iperf -s -p {port} &')
    time.sleep(2)

    print(f"Running iPerf Client from {sender} to {receiver}")
    # Run in background and for the full duration
    h1.cmd(f'iperf -c {h2.IP()} -t {duration} -p {port} -i 1 -b 50M -l 100 &')
    
    # Keep the thread alive for the duration
    time.sleep(duration)
    print(f"iPerf traffic from {sender} to {receiver} completed")
    
def bursty_traffic(net):
    
    hosts = [net.get(f'h{i}') for i in range(1, 31)]
    time.sleep(30)
    
    running_servers = set()
    print("Bursty Traffic Started")
    
    for i in range(5):
        # Select hosts that aren't currently waiting on commands
        available_hosts = [h for h in hosts if not h.waiting]
            
        if len(available_hosts) < 2:
            # Not enough available hosts, wait and try again
            time.sleep(1)
            continue
                
        # Choose server from available hosts
        server_host = random.choice(available_hosts)
        # Choose client from available hosts excluding the server
        client_host = random.choice([h for h in available_hosts if h != server_host])
            
        # Get unique port for each server to avoid conflicts
        server_port = 9000 + len(running_servers)
        server_id = f"{server_host.name}:{server_port}"
            
        # Start iperf server with a unique port
        server_host.sendCmd(f'iperf -s -p {server_port} > /dev/null 2>&1 &')
        # Need to wait for command to complete and get shell prompt back
        running_servers.add(server_id)
        server_host.waitOutput()

        # Start continuous client with varying bandwidths
        bandwidth = random.randint(5, 10)  # Increased to 1M-5M bps for more reliable traffic
        duration = random.randint(20, 40) 
            
        client_host.sendCmd(f'iperf -c {server_host.IP()} -p {server_port} -l 300 -t {duration} -b {bandwidth}M > /dev/null 2>&1 &')
        client_host.waitOutput()
        time.sleep(10)
    
    print("Bursty Traffic completed")
        
# Function to start SYN flood attack using hping3
def run_hping3_syn_flood(net, duration):
    hosts = [net.get(f'h{i}') for i in range(1, 31)]
    time.sleep(30)
    print("SYN Flood Attack Started")

    for i in range(1):
        # Select hosts that aren't currently waiting on commands
        available_hosts = [h for h in hosts if not h.waiting]
            
        if len(available_hosts) < 2:
            # Not enough available hosts, wait and try again
            time.sleep(1)
            continue
                
        # Choose the attacker from available hosts
        attacker = random.choice(available_hosts)
        # Choose the target from available hosts excluding itself and other attackers
        target = random.choice([h for h in available_hosts if h != attacker])
        
        attacker.cmd(f"hping3 -S -i u1000 -p 80 {target.IP()} & sleep 60; kill $!")
        attacker.waitOutput()
        time.sleep(20)
        
    print("SYN flood attack completed")

def generate_normal_traffic(net, duration=300):
    """
    Generates substantial background traffic across the network to simulate realistic internet activity.
    """
    hosts = [net.get(f'h{i}') for i in range(1, 31)]
    
    print("Starting enhanced normal background traffic...")
    start_time = time.time()
    end_time = start_time + duration
    
    # Track running servers to avoid starting multiple servers on the same host
    running_servers = set()
    
    # Keep generating additional dynamic traffic
    while time.time() < end_time:
        remaining = end_time - time.time()
        if remaining <= 0:
            break
            
        for i in range(random.randint(5,10)):
            # Select hosts that aren't currently waiting on commands
            available_hosts = [h for h in hosts if not h.waiting]
            
            if len(available_hosts) < 2:
                # Not enough available hosts, wait and try again
                time.sleep(1)
                continue
                
            # Choose server from available hosts
            server_host = random.choice(available_hosts)
            # Choose client from available hosts excluding the server
            client_host = random.choice([h for h in available_hosts if h != server_host])
            
            # Get unique port for each server to avoid conflicts
            server_port = 1000 + len(running_servers)
            server_id = f"{server_host.name}:{server_port}"
            
            # Start iperf server with a unique port
            server_host.sendCmd(f'iperf -s -p {server_port} > /dev/null 2>&1 &')
            # Need to wait for command to complete and get shell prompt back
            server_host.waitOutput()
            running_servers.add(server_id)
            
            # Start continuous client with varying bandwidths
            bandwidth = random.randint(5, 10)  # Increased to 10-50K bps for more reliable traffic
            duration2 = min(remaining, random.randint(30, 60))  # Ensure we don't exceed total duration1
            
            client_host.sendCmd(f'iperf -c {server_host.IP()} -p {server_port} -l 300 -t {duration2} -b {bandwidth}K > /dev/null 2>&1 &')
            client_host.waitOutput()
            
        # Wait between iterations to reduce control overhead
        time.sleep(5)

# Main function to control network and start traffic
def start_network():
    topo = CustomTopo()
    net = Mininet(topo=topo, controller=lambda name: RemoteController(name, ip='127.0.0.1', port=6633), link=TCLink)
    net.start()

    # Choose hosts dynamically
    sender, receiver = "h1", "h4"
    syn_attacker, syn_target = "h3", "h4"

    # Wait for controller to establish
    print("Waiting for the controller to establish connection...")
    time.sleep(5)

    # Traffic simulation duration in seconds
    simulation_duration = 300
    
    # Create threads for all types of traffic
    threads = [
        threading.Thread(target=run_iperf, args=(net, "h1", "h4", 8000,  300)),
        threading.Thread(target=run_iperf, args=(net, "h2", "h5", 8001,  300)),
        threading.Thread(target=bursty_traffic, args=(net,)),
        threading.Thread(target=generate_normal_traffic, args=(net, 300)),
        threading.Thread(target=run_hping3_syn_flood, args=(net, 300)),
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
