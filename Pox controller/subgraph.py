#!/usr/bin/env python
from pox.core import core
import pox.openflow.discovery as discovery
from pox.lib.util import dpid_to_str, str_to_dpid
from pox.web.webcore import SplitRequestHandler
import json
import re
from collections import deque

log = core.getLogger()

class TopologyManager(object):
    """
    Keeps track of the network topology and provides information about neighboring switches.
    """
    def __init__(self):
        # Listen to topology discovery events
        core.openflow_discovery.addListeners(self)
        # Store the adjacency information
        self.adjacency = {}
        # Map between switch names (s1, s2) and dpids
        self.switch_name_to_dpid = {}
        self.dpid_to_switch_name = {}
        # Register for ConnectionUp events to track switches
        core.openflow.addListenerByName("ConnectionUp", self._handle_ConnectionUp)
        log.info("Topology Manager initialized")
        
    def _handle_ConnectionUp(self, event):
        """Handle when a switch connects"""
        dpid = event.dpid
        dpid_str = dpid_to_str(dpid)
        # Extract switch number and create the switch name
        # Assuming the last octet or some part of DPID indicates the switch number
        switch_num = dpid & 0xFF  # Take the last byte for switch number
        switch_name = f"s{switch_num}"
        
        self.switch_name_to_dpid[switch_name] = dpid
        self.dpid_to_switch_name[dpid] = switch_name
        log.info(f"Switch {switch_name} (DPID: {dpid_str}) connected")
        
    def _handle_LinkEvent(self, event):
        """Handle discovery of links between switches"""
        l = event.link
        sw1 = l.dpid1
        sw2 = l.dpid2
        port1 = l.port1
        port2 = l.port2
        
        # Update adjacency
        if sw1 not in self.adjacency:
            self.adjacency[sw1] = {}
        if sw2 not in self.adjacency:
            self.adjacency[sw2] = {}
            
        if event.removed:
            # Link removed, remove from adjacency
            if port1 in self.adjacency[sw1]:
                del self.adjacency[sw1][port1]
            if port2 in self.adjacency[sw2]:
                del self.adjacency[sw2][port2]
            log.info(f"Link removed between {self.dpid_to_switch_name.get(sw1, sw1)}:{port1} and {self.dpid_to_switch_name.get(sw2, sw2)}:{port2}")
        else:
            # Link added
            self.adjacency[sw1][port1] = (sw2, port2)
            self.adjacency[sw2][port2] = (sw1, port1)
            log.info(f"Link added between {self.dpid_to_switch_name.get(sw1, sw1)}:{port1} and {self.dpid_to_switch_name.get(sw2, sw2)}:{port2}")
    
    def get_neighbors_by_name(self, switch_name, blast_radius=1):
        """
        Get neighboring switches for a given switch name (e.g., 's1') 
        with specified blast radius
        
        Args:
            switch_name (str): The name of the switch (e.g., 's1')
            blast_radius (int): How many hops to traverse (default 1)
            
        Returns:
            dict or list: Information about neighboring switches or error
        """
        if switch_name not in self.switch_name_to_dpid:
            return {"error": f"Switch {switch_name} not found"}
        
        dpid = self.switch_name_to_dpid[switch_name]
        return self.get_neighbors_with_blast_radius(dpid, blast_radius)
    
    def get_neighbors_with_blast_radius(self, start_dpid, blast_radius=1):
        """
        Get unique switches within specified blast radius of the start switch
    
        Args:
            start_dpid (int): DPID of the starting switch
            blast_radius (int): How many hops to traverse (default 1)
        
        Returns:
            list: Information about unique switches within the blast radius
        """
        if blast_radius < 1:
            blast_radius = 1
        
        # Track visited switches to avoid cycles
        visited = set()
    
        # Store switches organized by their distance from the start switch
        switches_by_radius = {}
    
        # Track switches we've already added to the result to avoid duplicates
        added_switches = set()
    
        # Use BFS to find switches within the blast radius
        queue = deque([(start_dpid, 1)])  # (switch_dpid, distance)
        visited.add(start_dpid)
        added_switches.add(start_dpid)
    
        while queue:
            current_dpid, distance = queue.popleft()
        
            # If we've reached the blast radius limit, don't explore further
            if distance > blast_radius:
                continue
            
            # Get direct neighbors of the current switch
            direct_neighbors = self.get_neighbors(current_dpid)
        
            # Add this switch's neighbors to the appropriate radius
            if distance not in switches_by_radius:
                switches_by_radius[distance] = []
            
            # Adding the neighboring switches to the added_switches list
            for neighbor in direct_neighbors:
                neighbor_dpid = neighbor["dpid"]
                if neighbor_dpid not in added_switches:
                    switches_by_radius[distance].append(neighbor)
                    added_switches.add(neighbor_dpid)
            
            # Add unvisited neighbors to the queue
            if distance < blast_radius:
                for neighbor in direct_neighbors:
                    neighbor_dpid = neighbor["dpid"]
                    if neighbor_dpid not in visited:
                        visited.add(neighbor_dpid)
                        queue.append((neighbor_dpid, distance + 1))
    
        # Flatten the results
        result = []
        for radius in range(1, blast_radius + 1):
            if radius in switches_by_radius:
                for switch in switches_by_radius[radius]:
                    # Add radius information to each switch
                    switch["radius"] = radius
                    result.append(switch)
    
        return result
        
    def get_neighbors(self, dpid):
        """Get neighboring switches of a given switch DPID"""
        if dpid not in self.adjacency:
            return []
            
        neighbors = []
        for port, (neighbor_dpid, neighbor_port) in self.adjacency[dpid].items():
            neighbor_name = self.dpid_to_switch_name.get(neighbor_dpid, f"unknown-{neighbor_dpid}")
            neighbors.append({
                "switch_name": neighbor_name,
                "dpid": neighbor_dpid,
                "local_port": port,
                "remote_port": neighbor_port
            })
        
        return neighbors

class NeighborAPIHandler(SplitRequestHandler):
    """
    REST API handler for querying neighboring switches
    """
    def do_GET(self):
        """Handle GET requests"""
        # Extract path parts and query parameters
        path_parts = self.path.strip("/").split("/")
        
        # Parse query parameters (if any)
        query_params = {}
        if '?' in self.path:
            path, query = self.path.split('?', 1)
            for param in query.split('&'):
                if '=' in param:
                    key, value = param.split('=', 1)
                    query_params[key] = value
                    
            # Update path_parts without query parameters
            path_parts = path.strip("/").split("/")
        
        # Get blast radius parameter, default to 1
        blast_radius = 1
        if 'blast_radius' in query_params:
            try:
                blast_radius = int(query_params['blast_radius'])
                if blast_radius < 1:
                    blast_radius = 1
            except ValueError:
                self.send_error_json(400, "Invalid blast_radius parameter. Must be a positive integer.")
                return
        
        # Check if this is a request for the neighbors API
        if len(path_parts) >= 2 and path_parts[0] == "api" and path_parts[1] == "neighbors":
            # Get switch name if provided
            switch_name = None
            if len(path_parts) > 2:
                switch_name = path_parts[2]
                
            self.handle_neighbors_request(switch_name, blast_radius)
        else:
            self.send_error(404, "Not Found")
    
    def handle_neighbors_request(self, switch_name=None, blast_radius=1):
        """Handle request for neighbors information"""
        if not switch_name:
            # If no switch is specified, return all switches
            switches = list(core.topology_manager.switch_name_to_dpid.keys())
            self.send_json({
                "switches": switches,
                "usage": "To get neighbors: /api/neighbors/[switch_name]?blast_radius=[1-n]"
            })
            return

        # Validate switch name format (e.g., "s1")
        match = re.match(r's(\d+)', switch_name)
        if not match:
            self.send_error_json(400, "Invalid switch name format. Use format 's1', 's2', etc.")
            return
        
        # Get neighbors with the specified blast radius
        neighbors = core.topology_manager.get_neighbors_by_name(switch_name, blast_radius)
        
        if isinstance(neighbors, dict) and "error" in neighbors:
            self.send_error_json(404, neighbors["error"])
        else:
            self.send_json({
                "anchor_switch": switch_name,
                "blast_radius": blast_radius,
                "neighbors": neighbors
            })
    
    def send_json(self, data):
        """Helper method to send JSON response"""
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data, indent=2).encode())
    
    def send_error_json(self, code, message):
        """Helper method to send error response as JSON"""
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        error_data = {"error": message}
        self.wfile.write(json.dumps(error_data).encode())

def launch():
    """
    Initialize and register the topology manager and API endpoint
    """
    # Make sure discovery is running
    if not core.hasComponent("openflow_discovery"):
        from pox.openflow.discovery import launch as discovery_launch
        discovery_launch()
    
    # Initialize the topology manager
    topo_manager = TopologyManager()
    core.register("topology_manager", topo_manager)
    
    # Initialize the webserver if not already running
    if not core.hasComponent("WebServer"):
        from pox.web.webcore import launch as webserver_launch
        webserver_launch()
    
    # Register the handler with the web server
    core.WebServer.set_handler("/", NeighborAPIHandler)
    
    log.info("Neighbor Switch API initialized at /api/neighbors/[switch_name]?blast_radius=[1-n]")
