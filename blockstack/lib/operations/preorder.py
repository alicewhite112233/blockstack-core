#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
    Blockstack
    ~~~~~
    copyright: (c) 2014-2015 by Halfmoon Labs, Inc.
    copyright: (c) 2016 by Blockstack.org

    This file is part of Blockstack

    Blockstack is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    Blockstack is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.
    You should have received a copy of the GNU General Public License
    along with Blockstack. If not, see <http://www.gnu.org/licenses/>.
"""

# from blockstack_utxo import get_unspents, broadcast_transaction, analyze_private_key, serialize_sign_and_broadcast
import virtualchain
from virtualchain.lib.blockchain.bitcoin import tx_serialize, script_hex_to_address, make_op_return_script, \
        calculate_change_amount, make_pay_to_address_script

from keylib import ECPrivateKey, ECPublicKey
from utilitybelt import is_hex
from binascii import hexlify, unhexlify

from ..b40 import b40_to_hex, is_b40
from ..config import *
from ..scripts import *
from ..hashing import hash_name
from ..blockchain import get_tx_inputs
from ..nameset import get_namespace_from_name

# consensus hash fields (ORDER MATTERS!)
FIELDS = [
     'preorder_name_hash',  # hash(name,sender,register_addr) 
     'consensus_hash',      # consensus hash at time of send
     'sender',              # scriptPubKey hex that identifies the principal that issued the preorder
     'sender_pubkey',       # if sender is a pubkeyhash script, then this is the public key
     'address',             # address from the sender's scriptPubKey
     'block_number',        # block number at which this name was preordered for the first time

     'op',                  # blockstack bytestring describing the operation
     'txid',                # transaction ID
     'vtxindex',            # the index in the block where the tx occurs
     'op_fee',              # blockstack fee (sent to burn address)
]

def build(name, script_pubkey, register_addr, consensus_hash, name_hash=None):
    """
    Takes a name, including the namespace ID (but not the id: scheme), a script_publickey to prove ownership
    of the subsequent NAME_REGISTER operation, and the current consensus hash for this block (to prove that the 
    caller is not on a shorter fork).
    
    Returns a NAME_PREORDER script.
    
    Record format:
    
    0     2  3                                              23             39
    |-----|--|----------------------------------------------|--------------|
    magic op  hash(name.ns_id,script_pubkey,register_addr)   consensus hash
    
    """
    
    if name_hash is None:

        # expect inputs to the hash
        if not is_b40( name ) or "+" in name or name.count(".") > 1:
           raise Exception("Name '%s' has non-base-38 characters" % name)
        
        # name itself cannot exceed LENGTHS['blockchain_id_name']
        if len(NAME_SCHEME) + len(name) > LENGTHS['blockchain_id_name']:
           raise Exception("Name '%s' is too long; exceeds %s bytes" % (name, LENGTHS['blockchain_id_name'] - len(NAME_SCHEME)))
    
        name_hash = hash_name(name, script_pubkey, register_addr=register_addr)

    script = 'NAME_PREORDER 0x%s 0x%s' % (name_hash, consensus_hash)
    hex_script = blockstack_script_to_hex(script)
    packaged_script = add_magic_bytes(hex_script)
    
    return packaged_script


def make_outputs( data, inputs, sender_addr, fee, format='bin' ):
    """
    Make outputs for a name preorder:
    [0] OP_RETURN with the name 
    [1] address with the NAME_PREORDER sender's address
    [2] pay-to-address with the *burn address* with the fee
    """
    
    op_fee = max(fee, DEFAULT_DUST_FEE)
    dust_fee = (len(inputs) + 2) * DEFAULT_DUST_FEE + DEFAULT_OP_RETURN_FEE
    dust_value = DEFAULT_DUST_FEE
     
    bill = op_fee
    
    return [
        # main output
        {"script_hex": make_op_return_script(data, format=format),
         "value": 0},
        
        # change address (can be subsidy key)
        {"script_hex": make_pay_to_address_script(sender_addr),
         "value": calculate_change_amount(inputs, bill, dust_fee)},
        
        # burn address
        {"script_hex": make_pay_to_address_script(BLOCKSTACK_BURN_ADDRESS),
         "value": op_fee}
    ]


def state_transition(name, private_key, register_addr, consensus_hash, fee, subsidy_public_key=None):
    """
    Builds and broadcasts a preorder transaction.

    @subsidy_public_key: if given, the public part of the subsidy key 
    """

    namespace_id = get_namespace_from_name( name )
    blockchain_name = namespace_to_blockchain( namespace_id )
    
    # sanity check 
    if subsidy_public_key is None and private_key is None:
        raise Exception("Missing both client public and private key")

    pubk = None
    script_pubkey = None    # to be mixed into preorder hash
    
    if subsidy_public_key is not None:
        # subsidizing
        pubk = ECPublicKey( subsidy_public_key )
        script_pubkey = get_script_pubkey( subsidy_public_key )

    else:
        # ordering directly
        pubk = ECPrivateKey( private_key ).public_key()
        script_pubkey = get_script_pubkey( public_key )
       
    from_address = pubk.address()
    inputs = get_tx_inputs( blockchain_name, from_address )
    
    nulldata = build( name, script_pubkey, register_addr, consensus_hash, testset=testset)
    outputs = make_outputs(nulldata, inputs, from_address, fee, format='hex')
    return inputs, outputs


def broadcast(name, private_key, register_addr, consensus_hash, blockchain_client, fee, blockchain_broadcaster=None, subsidy_public_key=None, tx_only=False, testset=False):
    """
    Builds and broadcasts a preorder transaction.

    @subsidy_public_key: if given, the public part of the subsidy key 
    """

    if subsidy_public_key is not None:
        # subsidizing, and only want the tx 
        tx_only = True
    
    # sanity check 
    if subsidy_public_key is None and private_key is None:
        raise Exception("Missing both client public and private key")
    
    if blockchain_broadcaster is None:
        blockchain_broadcaster = blockchain_client 

    pubk = None
    script_pubkey = None    # to be mixed into preorder hash
    
    if subsidy_public_key is not None:
        # subsidizing
        pubk = ECPublicKey( subsidy_public_key )
        script_pubkey = get_script_pubkey( subsidy_public_key )

    else:
        # ordering directly
        pubk = ECPrivateKey( private_key ).public_key()
        script_pubkey = get_script_pubkey( public_key )
       
    from_address = pubk.address()
    inputs = get_unspents( from_address, blockchain_client )
    
    nulldata = build( name, script_pubkey, register_addr, consensus_hash, testset=testset)
    outputs = make_outputs(nulldata, inputs, from_address, fee, format='hex')
    
    if tx_only:

        unsigned_tx = tx_serialize( inputs, outputs )
        return {"unsigned_tx": unsigned_tx}
    
    else:
        # serialize, sign, and broadcast the tx
        signed_tx = tx_serialize_sign( inputs, outputs, private_key )
        response = broadcast_transaction( inputs, outputs, blockchain_broadcaster )
        # response = serialize_sign_and_broadcast(inputs, outputs, private_key_obj, blockchain_broadcaster)
        response.update({'data': nulldata})
        return response


def parse(bin_payload):
    """
    Parse a name preorder.
    NOTE: bin_payload *excludes* the leading 3 bytes (magic + op) returned by build.
    """
    
    if len(bin_payload) != LENGTHS['preorder_name_hash'] + LENGTHS['consensus_hash']:
        return None 

    name_hash = hexlify( bin_payload[0:LENGTHS['preorder_name_hash']] )
    consensus_hash = hexlify( bin_payload[LENGTHS['preorder_name_hash']:] )
    
    return {
        'opcode': 'NAME_PREORDER',
        'preorder_name_hash': name_hash,
        'consensus_hash': consensus_hash
    }


def get_fees( inputs, outputs ):
    """
    Given a transaction's outputs, look up its fees:
    * the first output must be an OP_RETURN, and it must have a fee of 0.
    # the second must be the change address
    * the third must be a burn fee to the burn address.
    
    Return (dust fees, operation fees) on success 
    Return (None, None) on invalid output listing
    """
    if len(outputs) != 3:
        log.debug("Expected 3 outputs; got %s" % len(outputs))
        return (None, None)
    
    # 0: op_return
    if not tx_output_is_op_return( outputs[0] ):
        log.debug("outputs[0] is not an OP_RETURN")
        return (None, None) 
    
    if outputs[0]["value"] != 0:
        log.debug("outputs[0] has value %s'" % outputs[0]["value"])
        return (None, None) 
    
    # 1: change address 
    if script_hex_to_address( outputs[1]["script_hex"] ) is None:
        log.error("outputs[1] has no decipherable change address")
        return (None, None)
    
    # 2: burn address 
    addr_hash = script_hex_to_address( outputs[2]["script_hex"] )
    if addr_hash is None:
        log.error("outputs[2] has no decipherable burn address")
        return (None, None) 
    
    if addr_hash != BLOCKSTACK_BURN_ADDRESS:
        log.error("outputs[2] is not the burn address")
        return (None, None)
    
    dust_fee = (len(inputs) + 2) * DEFAULT_DUST_FEE + DEFAULT_OP_RETURN_FEE
    op_fee = outputs[2]["value"]
    
    return (dust_fee, op_fee)
