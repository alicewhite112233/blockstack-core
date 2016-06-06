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

# from blockstack_utxo import get_unspents, broadcast_transaction, analyze_private_key
import virtualchain
from virtualchain.lib.blockchain.bitcoin import make_op_return_script, \
        calculate_change_amount, make_pay_to_address_script

from keylib import ECPrivateKey, ECPublicKey
from utilitybelt import is_hex
from binascii import hexlify, unhexlify

from ..b40 import b40_to_hex, bin_to_b40, is_b40
from ..config import *
from ..scripts import *
from ..blockchain import get_tx_inputs

# consensus hash fields (none for announcements)
FIELDS = []

def build(message_hash):
    """
     
    Record format:
    
    0    2  3                             23
    |----|--|-----------------------------|
    magic op   message hash (160-bit)
    
    """
   
    if len(message_hash) != 40:
        raise Exception("Invalid hash: not 20 bytes")

    if not is_hex(message_hash):
        raise Exception("Invalid hash: not hex")

    readable_script = "ANNOUNCE 0x%s" % (message_hash)
    hex_script = blockstack_script_to_hex(readable_script)
    packaged_script = add_magic_bytes(hex_script)
    
    return packaged_script 


def make_outputs( data, inputs, change_address, pay_fee=True ):
    """
    Make outputs for an announcement.
    """

    dust_fee = None
    op_fee = None
    dust_value = None 
    
    if pay_fee:
        dust_fee = (len(inputs) + 1) * DEFAULT_DUST_FEE + DEFAULT_OP_RETURN_FEE
        op_fee = DEFAULT_DUST_FEE
        dust_value = DEFAULT_DUST_FEE
    
    else:
        # will be subsidized
        dust_fee = 0
        op_fee = 0
        dust_value = 0
   
    return [
        # main output
        {"script_hex": make_op_return_script(data, format='hex'),
         "value": 0},
        
        # change output
        {"script_hex": make_pay_to_address_script(change_address),
         "value": calculate_change_amount(inputs, op_fee, dust_fee)}
    ]


def state_transition(blockchain_name, message_hash, private_key, user_public_key=None):
    
    # sanity check 
    pay_fee = True
    if user_public_key is not None:
        pay_fee = False

    if user_public_key is None and private_key is None:
        raise Exception("Missing both public and private key")
    
    if len(message_hash) != 40:
        raise Exception("Invalid message hash: not 20 bytes")

    if not is_hex( message_hash ):
        raise Exception("Invalid message hash: not hex")

    pubk = None 

    if user_public_key is not None:
        # subsidizing 
        pubk = ECPublicKey( user_public_key )

    else:
        # ordering directly
        pubk = ECPrivateKey( private_key ).public_key()

    from_address = pubk.address()
    inputs = get_tx_inputs( blockchain_name, from_address )

    nulldata = build(message_hash)
    outputs = make_outputs( nulldata, inputs, from_address, pay_fee=pay_fee )
    return inputs, outputs
  

def broadcast(message_hash, private_key, blockchain_client, testset=False, blockchain_broadcaster=None, user_public_key=None, tx_only=False):
    
    # sanity check 
    pay_fee = True
    if user_public_key is not None:
        pay_fee = False
        tx_only = True

    if user_public_key is None and private_key is None:
        raise Exception("Missing both public and private key")
    
    if not tx_only and private_key is None:
        raise Exception("Need private key for broadcasting")
    
    if len(message_hash) != 40:
        raise Exception("Invalid message hash: not 20 bytes")

    if not is_hex( message_hash ):
        raise Exception("Invalid message hash: not hex")

    if blockchain_broadcaster is None:
        blockchain_broadcaster = blockchain_client 
    
    pubk = None 

    if user_public_key is not None:
        # subsidizing 
        pubk = ECPublicKey( user_public_key )

    else:
        # ordering directly
        pubk = ECPrivateKey( private_key ).public_key()

    from_address = pubk.address()
    inputs = get_unspents( from_address, blockchain_client )

    nulldata = build(message_hash, testset=testset)
    outputs = make_outputs( nulldata, inputs, from_address, pay_fee=pay_fee )
   
    if tx_only:
       
        unsigned_tx = tx_serialize( inputs, outputs )
        return {'unsigned_tx': unsigned_tx}

    else:
       
        signed_tx = tx_serialize_sign( inputs, outputs, private_key )
        response = broadcast_transaction( signed_tx, blockchain_broadcaster )
        response.update({'data': nulldata})
        return response


def parse(bin_payload):    
    """
    Interpret a block's nulldata back into a SHA256.  The first three bytes (2 magic + 1 opcode)
    will not be present in bin_payload.
    """
    
    message_hash = hexlify(bin_payload)
    if not is_hex( message_hash ):
        log.error("Not a message hash")
        return None 

    if len(message_hash) != 40:
        log.error("Not a 160-bit hash")
        return None 

    return {
       'opcode': 'ANNOUNCE',
       'message_hash': message_hash
    }


def get_fees( inputs, outputs ):
    """
    Given a transaction's outputs, look up its fees:
    * there should be two outputs: the OP_RETURN and change address
    
    Return (dust fees, operation fees) on success 
    Return (None, None) on invalid output listing
    """
    if len(outputs) != 2:
        return (None, None)
    
    # 0: op_return
    if not tx_output_is_op_return( outputs[0] ):
        return (None, None) 
    
    if outputs[0]["value"] != 0:
        return (None, None) 
    
    # 1: change address 
    if script_hex_to_address( outputs[1]["script_hex"] ) is None:
        return (None, None)
    
    dust_fee = (len(inputs) + 1) * DEFAULT_DUST_FEE + DEFAULT_OP_RETURN_FEE
    op_fee = 0
    
    return (dust_fee, op_fee)

