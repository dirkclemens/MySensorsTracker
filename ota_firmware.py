# -*- coding: utf-8 -*-
#
# @file          ota_firmware.py
# Author       : Dirk (adapted from pymysensors)
# Created      : Dec 24 2025
#
# OTA Firmware Update Handler for MySensors

import binascii
import logging
import os
import struct
from intelhex import IntelHex, IntelHexError
import crcmod

FIRMWARE_BLOCK_SIZE = 16
_LOGGER = logging.getLogger(__name__)


def compute_crc16(data):
    """Compute CRC16 MODBUS of data and return an int."""
    crc16_func = crcmod.predefined.mkPredefinedCrcFun('modbus')
    return crc16_func(data)


def fw_hex_to_int(hex_str, words):
    """Unpack hex string into integers.
    
    Use little-endian and unsigned int format. Specify number of words to
    unpack with argument words.
    """
    return struct.unpack(f"<{words}H", binascii.unhexlify(hex_str))


def fw_int_to_hex(*args):
    """Pack integers into hex string.
    
    Use little-endian and unsigned int format.
    """
    return binascii.hexlify(struct.pack(f"<{len(args)}H", *args)).decode("utf-8")


def load_firmware(path):
    """Open firmware file and return a binary string."""
    fname = os.path.realpath(path)
    exists = os.path.isfile(fname)
    if not exists or not os.access(fname, os.R_OK):
        _LOGGER.error("Firmware path %s does not exist or is not readable", path)
        return None
    try:
        intel_hex = IntelHex()
        with open(path, "r", encoding="utf-8") as file_handle:
            intel_hex.fromfile(file_handle, format="hex")
        return intel_hex.tobinstr()
    except (IntelHexError, TypeError, ValueError) as exc:
        _LOGGER.error("Firmware not valid, check the hex file at %s: %s", path, exc)
        return None


def prepare_firmware(bin_string):
    """Check that firmware is valid and return dict with binary data."""
    pads = len(bin_string) % 128  # 128 bytes per page for atmega328
    for _ in range(128 - pads):  # pad up to even 128 bytes
        bin_string += b"\xff"
    fware = {
        "blocks": int(len(bin_string) / FIRMWARE_BLOCK_SIZE),
        "crc": compute_crc16(bin_string),
        "data": bin_string,
    }
    return fware


class OTAFirmwareManager:
    """Manage OTA Firmware Updates for MySensors Nodes."""
    
    def __init__(self):
        """Initialize OTA Firmware Manager."""
        self.firmware_store = {}  # (fw_type, fw_ver) -> firmware dict
        self.requested_nodes = {}  # node_id -> (fw_type, fw_ver)
        self.unstarted_nodes = {}  # node_id -> (fw_type, fw_ver)
        self.started_nodes = {}   # node_id -> (fw_type, fw_ver)
        
    def load_firmware(self, fw_type, fw_ver, fw_path):
        """Load firmware from hex file.
        
        Args:
            fw_type: Firmware type (int)
            fw_ver: Firmware version (int)
            fw_path: Path to .hex firmware file
            
        Returns:
            bool: True if loaded successfully
        """
        try:
            fw_type, fw_ver = int(fw_type), int(fw_ver)
        except ValueError:
            _LOGGER.error("Firmware type %s or version %s not valid, use integers", fw_type, fw_ver)
            return False
            
        fw_bin = load_firmware(fw_path)
        if not fw_bin:
            return False
            
        fware = prepare_firmware(fw_bin)
        self.firmware_store[(fw_type, fw_ver)] = fware
        _LOGGER.info("Loaded firmware type %s version %s: %d blocks, CRC %04X", 
                    fw_type, fw_ver, fware["blocks"], fware["crc"])
        return True
    
    def delete_firmware(self, fw_type, fw_ver):
        """Delete firmware from memory.
        
        Args:
            fw_type: Firmware type (int)
            fw_ver: Firmware version (int)
        """
        try:
            fw_type, fw_ver = int(fw_type), int(fw_ver)
        except ValueError:
            _LOGGER.error("Firmware type %s or version %s not valid", fw_type, fw_ver)
            return
            
        if (fw_type, fw_ver) in self.firmware_store:
            del self.firmware_store[(fw_type, fw_ver)]
            _LOGGER.info("Deleted firmware type %s version %s", fw_type, fw_ver)
        
    def request_update(self, node_id, fw_type, fw_ver):
        """Request firmware update for a node.
        
        Args:
            node_id: Node ID to update
            fw_type: Firmware type (int)
            fw_ver: Firmware version (int)
            
        Returns:
            bool: True if update requested successfully
        """
        try:
            fw_type, fw_ver = int(fw_type), int(fw_ver)
        except ValueError:
            _LOGGER.error("Firmware type %s or version %s not valid", fw_type, fw_ver)
            return False
            
        if (fw_type, fw_ver) not in self.firmware_store:
            _LOGGER.error("No firmware type %s version %s loaded", fw_type, fw_ver)
            return False
            
        # Remove from other states
        self.unstarted_nodes.pop(node_id, None)
        self.started_nodes.pop(node_id, None)
        
        # Mark as requested
        self.requested_nodes[node_id] = (fw_type, fw_ver)
        _LOGGER.info("Node %d requested for firmware update: type %s version %s", 
                    node_id, fw_type, fw_ver)
        return True
        
    def handle_firmware_config_request(self, node_id, payload):
        """Handle ST_FIRMWARE_CONFIG_REQUEST (type=0) from node.
        
        Args:
            node_id: Node ID requesting config
            payload: Hex payload from node
            
        Returns:
            str or None: Response payload or None
        """
        try:
            (req_fw_type, req_fw_ver, req_blocks, req_crc, bloader_ver) = fw_hex_to_int(payload, 5)
        except:
            _LOGGER.error("Invalid firmware config request from node %d", node_id)
            return None
            
        _LOGGER.debug("Node %d firmware config request: type %s ver %s blocks %d CRC %04X bootloader %s",
                     node_id, req_fw_type, req_fw_ver, req_blocks, req_crc, bloader_ver)
        
        # Get firmware for this node
        fw_id = (self.requested_nodes.get(node_id) or 
                self.unstarted_nodes.get(node_id))
        
        if fw_id is None:
            _LOGGER.debug("Node %d not scheduled for firmware update", node_id)
            return None
            
        fw_type, fw_ver = fw_id
        fware = self.firmware_store.get((fw_type, fw_ver))
        
        if fware is None:
            _LOGGER.error("No firmware type %s version %s found", fw_type, fw_ver)
            return None
            
        # Move to unstarted if was in requested
        self.requested_nodes.pop(node_id, None)
        self.unstarted_nodes[node_id] = (fw_type, fw_ver)
        
        _LOGGER.info("Node %d updating from type %s ver %s to type %s ver %s",
                    node_id, req_fw_type, req_fw_ver, fw_type, fw_ver)
        
        # Response: fw_type, fw_ver, blocks, crc (ST_FIRMWARE_CONFIG_RESPONSE = 1)
        return fw_int_to_hex(fw_type, fw_ver, fware["blocks"], fware["crc"])
        
    def handle_firmware_request(self, node_id, payload):
        """Handle ST_FIRMWARE_REQUEST (type=2) from node.
        
        Args:
            node_id: Node ID requesting block
            payload: Hex payload from node
            
        Returns:
            str or None: Response payload or None
        """
        try:
            req_fw_type, req_fw_ver, req_block = fw_hex_to_int(payload, 3)
        except:
            _LOGGER.error("Invalid firmware request from node %d", node_id)
            return None
            
        _LOGGER.debug("Node %d firmware block request: type %s ver %s block %d",
                     node_id, req_fw_type, req_fw_ver, req_block)
        
        # Get firmware for this node
        fw_id = (self.unstarted_nodes.get(node_id) or 
                self.started_nodes.get(node_id))
        
        if fw_id is None:
            _LOGGER.debug("Node %d not in firmware update", node_id)
            return None
            
        fw_type, fw_ver = req_fw_type, req_fw_ver  # Use requested version
        fware = self.firmware_store.get((fw_type, fw_ver))
        
        if fware is None:
            _LOGGER.error("No firmware type %s version %s found", fw_type, fw_ver)
            return None
            
        # Move to started
        self.unstarted_nodes.pop(node_id, None)
        self.started_nodes[node_id] = (fw_type, fw_ver)
        
        # Get block data
        start = req_block * FIRMWARE_BLOCK_SIZE
        end = start + FIRMWARE_BLOCK_SIZE
        blk_data = fware["data"][start:end]
        
        # Response: fw_type, fw_ver, block, data (ST_FIRMWARE_RESPONSE = 3)
        payload = fw_int_to_hex(fw_type, fw_ver, req_block)
        payload += binascii.hexlify(blk_data).decode("utf-8")
        
        _LOGGER.debug("Node %d sending block %d/%d", node_id, req_block, fware["blocks"]-1)
        return payload
        
    def is_reboot_required(self, node_id):
        """Check if node should be rebooted for firmware update.
        
        Args:
            node_id: Node ID to check
            
        Returns:
            bool: True if reboot required
        """
        return node_id in self.requested_nodes
        
    def get_firmware_list(self):
        """Get list of all loaded firmware.
        
        Returns:
            list: List of (fw_type, fw_ver, blocks, crc) tuples
        """
        result = []
        for (fw_type, fw_ver), fware in self.firmware_store.items():
            result.append((fw_type, fw_ver, fware["blocks"], fware["crc"]))
        return result
        
    def get_node_status(self, node_id):
        """Get firmware update status for a node.
        
        Args:
            node_id: Node ID
            
        Returns:
            str: 'requested', 'unstarted', 'started', or None
        """
        if node_id in self.requested_nodes:
            return 'requested'
        elif node_id in self.unstarted_nodes:
            return 'unstarted'
        elif node_id in self.started_nodes:
            return 'started'
        return None
