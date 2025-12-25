#!/usr/bin/env python3
"""
Test script to send reboot command to MySensors Gateway

Usage:
  venv/bin/python test_reboot.py --test              # Test message format only
  venv/bin/python test_reboot.py --send <nid> [...]  # Send reboot to one or more nodes

"""

import sys
import socket
import time
import mysensors

GATEWAY_HOST = "192.168.2.211"
GATEWAY_PORT = 5003

def test_reboot_message(node_id):
    """Generate and print reboot message"""
    ack = 0
    message = f"{node_id};255;{mysensors.Commands.C_INTERNAL};{ack};{mysensors.Internal.I_REBOOT};"
    print(f"Reboot message for node {node_id}:")
    print(f"  Message: {message}")
    print(f"  C_INTERNAL value: {mysensors.Commands.C_INTERNAL}")
    print(f"  I_REBOOT value: {mysensors.Internal.I_REBOOT}")
    print()
    print("Expected format: node_id;255;3;0;13;")
    print(f"Actual format:   {message}")
    print()
    return message

def send_reboot_to_gateway(node_id):
    """Actually send reboot command to MySensors Gateway"""
    ack = 0
    message = f"{node_id};255;{mysensors.Commands.C_INTERNAL};{ack};{mysensors.Internal.I_REBOOT};"
    
    print(f"Sending reboot command to node {node_id}...")
    print(f"Gateway: {GATEWAY_HOST}:{GATEWAY_PORT}")
    print(f"Message: {message}")
    
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5.0)
        sock.connect((GATEWAY_HOST, GATEWAY_PORT))
        
        # Send message with newline as per MySensors protocol
        sock.sendall((message + "\n").encode('utf-8'))
        print(f"✓ Successfully sent reboot command to node {node_id}")
        
        sock.close()
        return True
        
    except socket.timeout:
        print(f"✗ Error: Connection timeout to {GATEWAY_HOST}:{GATEWAY_PORT}")
        return False
    except ConnectionRefusedError:
        print(f"✗ Error: Connection refused to {GATEWAY_HOST}:{GATEWAY_PORT}")
        print("  Is the gateway running?")
        return False
    except Exception as e:
        print(f"✗ Error: {e}")
        return False

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    
    if sys.argv[1] == '--test':
        # Test message format only
        print("Testing message format for various node IDs:\n")
        for nid in [7, 44, 103, 108, 121]:
            msg = test_reboot_message(nid)
            print(f"Match: {msg == f'{nid};255;3;0;13;'}")
            print("-" * 60)
    
    elif sys.argv[1] == '--send':
        # Send actual reboot command to one or more nodes
        if len(sys.argv) < 3:
            print("Usage: test_reboot.py --send <node_id> [node_id2] [node_id3] ...")
            sys.exit(1)
        
        node_ids = []
        for arg in sys.argv[2:]:
            try:
                node_id = int(arg)
                if node_id < 0 or node_id > 254:
                    print(f"Error: Node ID {node_id} must be between 0 and 254")
                    sys.exit(1)
                node_ids.append(node_id)
            except ValueError:
                print(f"Error: '{arg}' is not a valid node ID")
                sys.exit(1)
        
        print(f"Sending reboot commands to {len(node_ids)} node(s): {', '.join(map(str, node_ids))}\n")
        
        results = []
        for i, node_id in enumerate(node_ids):
            if i > 0:
                print()  # Add blank line between nodes
                time.sleep(0.5)  # Small delay between commands
            
            success = send_reboot_to_gateway(node_id)
            results.append((node_id, success))
        
        # Summary
        print("\n" + "=" * 60)
        print("Summary:")
        successful = sum(1 for _, success in results if success)
        failed = len(results) - successful
        
        for node_id, success in results:
            status = "✓" if success else "✗"
            print(f"  {status} Node {node_id}")
        
        print(f"\nTotal: {successful} successful, {failed} failed")
        sys.exit(0 if failed == 0 else 1)
    
    else:
        print(__doc__)
        sys.exit(1)
