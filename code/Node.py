#!/usr/bin/python3
import asyncio
import getopt

import aiomas
import hashlib
import logging
import sys

CHORD_FINGER_TABLE_SIZE = 8 # TODO: 256
CHORD_RING_SIZE = 2**CHORD_FINGER_TABLE_SIZE  # Maximum number of addresses in the Chord network

def in_interval(search_id, node_left, node_right, inclusive_left=False, inclusive_right=False):
    """
    Interval checks.
    """
    if inclusive_left:
        node_left = (node_left - 1) % CHORD_RING_SIZE
    if inclusive_right:
        node_right = (node_right + 1) % CHORD_RING_SIZE

    if node_left < node_right:
        return node_left < search_id < node_right
    elif node_left > node_right:
        # Circle wrapped around: separately regard cases for node_id > 0 and node_id < 0
        return (0 <= search_id < node_right) or \
               (node_left < search_id < CHORD_RING_SIZE)  # might be buggy, double check ranges!
    else:
        return False

# def deserialize_minimal_node(obj):
#     predecessor = None
#     if "predecessor" in obj:
#         predecessor = Node(None, None)
#         predecessor.setup_node(node_id=obj["predecessor"]["node_id"], node_address=obj["predecessor"]["node_address"])
#
#     node = Node(None, None)
#     node.setup_node(node_id=obj["node_id"], node_address=obj["node_address"], predecessor=predecessor)
#
#     return node


class Finger(object):
    def __init__(self, start_id, successor=None):
        self.startID = start_id
        self.successor = successor

    def __repr__(self):
        return 'Start: ' + str(self.startID) + ', SuccessorID: ' + str(self.successor.nodeId)


class Node(aiomas.Agent):

    def __init__(self, container, node_address):
        # Async RPC init
        super().__init__(container, node_address)
        self.node_address = node_address
        # Logging
        self.log = logging.getLogger(__name__)
        self.log.info("Node server listening on %s.", node_address)
        # Node state
        self.fingertable = []

    @asyncio.coroutine
    def setup_node(self, node_id=None, node_address=None, bootstrap_address=None, predecessor=None):
        """
        Set ups all internal state variables needed for operation.
        Needs to be called previously to any other function or RPC call.
        """
        self.id = node_id or self.generate_key(self.node_address)
        self.node_address = self.node_address or node_address   # only overwrite address if created by deserialization
        self.bootstrap_address = bootstrap_address
        self.log.info("[Configuration]  node_id: %d, bootstrap_node: %s", self.id, self.bootstrap_address)

        self.successor = self.as_dict()   # Todo: fix dependencies (should be set to None here and initialized properly)
        if predecessor is None:
            self.predecessor = self.as_dict()
        else:
            self.predecessor = predecessor

        # Create first version of finger table to prevent crash during message forward when debugging
        self.init_finger_table()

    def as_dict(self, serialize_neighbors=False):
        dict_node = {
            "node_id": self.id,
            "node_address": self.node_address,
        }
        if serialize_neighbors and self.successor:
            dict_node["successor"] = self.successor
        if serialize_neighbors and self.predecessor:
            dict_node["predecessor"] = self.predecessor

        return dict_node


    def to_object(obj):
        return Node(None, None)

    @staticmethod
    def generate_key(address):
        # TODO: public key hash instead of protocol + IP address + port
        # TODO: remove modulo
        return int(hashlib.sha256(address.encode()).hexdigest(), 16) % CHORD_RING_SIZE

    def get_entry(self, position):
        # Todo: rethink whether this makes sense
        addition = 2**(position-1)
        entry = (self.id + addition) % CHORD_RING_SIZE
        return entry

    @aiomas.expose
    def get_node_id(self):
        return self.id

    def print_finger_table(self):
        print(self.fingertable)

    def init_finger_table(self):
        # if self.bootstrap_address is None:
        self.fingertable = []
        for k in range(0, CHORD_FINGER_TABLE_SIZE):
            # entry = self
            # self.fingertable.append(Finger((self.id + 2**k) % (CHORD_RING_SIZE), entry))
            entry = {
                "start": ((self.id + 2**k) % CHORD_RING_SIZE),
                "successor": self.as_dict()
            }
            # TODO: add successor if not bootstrap node
            self.fingertable.append(entry)

        self.log.info("Fingertable: %s", str(self.fingertable)+"\n\n")
        if self.bootstrap_address is None:
            # We are the bootstrap node or start a new Chord network
            self.successor = self.predecessor = self.as_dict()

    def find_successor(self, node_id):
        node = self.find_predecessor(node_id)
        return node["successor"]  # Attention: relies on available successor information which has to be
                                  # retrieved by closest_preceding_finger()

    def find_predecessor(self, node_id):
        # Special case: id we are looking for is managed by our immediate successor
        if in_interval(node_id, self.id, self.successor["node_id"]):
            return self

        selected_node = self.as_dict(serialize_neighbors=True)
        while not in_interval(node_id, selected_node["node_id"], selected_node["successor"]["node_id"], inclusive_right=True):
            if selected_node.id == self.id:
                selected_node = self.__get_closest_preceding_finger(node_id)  # deadlocking right now
                # TODO: break if we are the one
            else:
                # For all other nodes, we have to do a RPC here
                self.log.warn("RPC not implemented here.")
                break

        return selected_node

    def __get_closest_preceding_finger(self, node_id):
        # Find closest preceding finger from m -> 0
        for k in range(CHORD_FINGER_TABLE_SIZE - 1, -1, -1):
            finger_successor = self.fingertable[k]["successor"]
            self.log.debug("Iterate finger %d: %d in %s", k, node_id, self.fingertable[k])

            if in_interval(finger_successor["node_id"], self.id, node_id):
                return finger_successor  # BUG: successor information missing, maybe need additional RPC here

        return self.as_dict(serialize_neighbors=True)

    ### RPC wrappers ###
    @aiomas.expose
    def get_closest_preceding_finger(self, node_id):
        # TODO: prevent malicious contents
        return self.__get_closest_preceding_finger(node_id)

    ### RPC tests ###
    @asyncio.coroutine
    def test_get_node_id(self, addr):
        # RPC to remote node
        remote_agent = yield from self.container.connect(addr)
        id = yield from remote_agent.get_node_id()
        print("%s got answer from %s: ID is %d" % (self.node_address, addr, id))

    @asyncio.coroutine
    def test_get_closest_preceding_finger(self, addr, node_id):
        # RPC to remote node
        remote_agent = yield from self.container.connect(addr)
        res_node = yield from remote_agent.get_closest_preceding_finger(node_id)
        print("%s got answer from %s: closest node is %s" % (self.node_address, addr, str(res_node)))


"""
Main application
"""
# Setup logging
logging.basicConfig(format='[%(levelname)s:%(threadName)s:%(name)s:%(funcName)s] %(message)s', level=logging.INFO)

# Parse console arguments
opts, args = getopt.getopt(sys.argv[1:], "p:s:c:")
port = -1
port_start = -1
host_count = -1
for key, val in opts:
    if key == "-p":
        port = int(val)
    elif key == "-s":
        port_start = int(val)
    elif key == "-c":
        host_count = int(val)

print("Port", port)
# Testing: First port is bootstrap node
bootstrap_addr = ("tcp://localhost:" + str(port_start) + "/0") if port != port_start else None
print("Address/Port of Bootstrap Node: ", bootstrap_addr)
print("-------------------")

# Define multiple agents per node for accepting RPCs
c = aiomas.Container(("localhost", port))
nodes = [c.spawn(Node) for i in range(2)]
# Start async server
loop = asyncio.get_event_loop()
loop.run_until_complete(nodes[0].setup_node(bootstrap_address=bootstrap_addr))
loop.run_until_complete(nodes[1].setup_node(bootstrap_address=bootstrap_addr))
# Test RPC calls within the same node
loop.run_until_complete(nodes[1].test_get_node_id(nodes[0].addr))

# Test RPC calls to bootstrap node (first port)
if bootstrap_addr is not None:
    loop.run_until_complete(nodes[1].test_get_node_id(bootstrap_addr))
    loop.run_until_complete(nodes[1].test_get_closest_preceding_finger(bootstrap_addr, 123))

loop.run_forever()
c.shutdown()