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

from blockstack_utxo import get_unspents, broadcast_transaction
from keylib import ECPrivateKey, ECPublicKey
from utilitybelt import is_hex
from binascii import hexlify, unhexlify

from ..b40 import b40_to_hex, bin_to_b40, is_b40
from ..config import *
from ..scripts import *
from ..hashing import hash256_trunc128

from ..nameset import NAMEREC_FIELDS, get_namespace_from_name
from ..blockchain import get_tx_inputs

import virtualchain
from virtualchain import hex_hash160
from virtualchain.lib.blockchain.bitcoin import script_hex_to_address, make_op_return_script, tx_serialize, tx_serialize_and_sign, tx_output_is_op_return

log = virtualchain.get_logger("blockstack-server")

# consensus hash fields (ORDER MATTERS!) 
FIELDS = NAMEREC_FIELDS + [
    'name_hash',            # hash(name,consensus_hash)
    'consensus_hash'        # consensus hash when this update was sent
]

def update_sanity_test( name, consensus_hash, data_hash ):
    """
    Verify the validity of an update's data

    Return True if valid
    Raise exception if not
    """
    
    if name is not None and (not is_b40( name ) or "+" in name or name.count(".") > 1):
       raise Exception("Name '%s' has non-base-38 characters" % name)
   
    if data_hash is not None and not is_hex( data_hash ):
       raise Exception("Invalid hex string '%s': not hex" % (data_hash))
    
    if len(data_hash) != 2 * LENGTHS['update_hash']:
       raise Exception("Invalid hex string '%s': bad length" % (data_hash))

    return True


def build(name, consensus_hash, data_hash=None):
    """
    Takes in the name to update the data for and the data update itself.
    Name must include the namespace ID, but not the scheme.
    
    Record format:
    
    0     2  3                                   19                      39
    |-----|--|-----------------------------------|-----------------------|
    magic op  hash128(name.ns_id,consensus hash) hash160(data)
    """

    rc = update_sanity_test( name, consensus_hash, data_hash )
    if not rc:
        raise Exception("Invalid update data")
    
    hex_name = hash256_trunc128( name + consensus_hash )
    
    readable_script = 'NAME_UPDATE 0x%s 0x%s' % (hex_name, data_hash)
    hex_script = blockstack_script_to_hex(readable_script)
    packaged_script = add_magic_bytes(hex_script)

    return packaged_script


def make_outputs( data, inputs, change_address, pay_fee=True ):
    """
    Make outputs for an update.
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


def state_transition(name, data_hash, consensus_hash, private_key, user_public_key=None):
    """
    Write a name update into the blockchain.
    Returns a JSON object with 'data' set to the nulldata and 'transaction_hash' set to the transaction hash on success.
    """
    
    namespace_id = get_namespace_from_name(name)
    blockchain_name = namespace_to_blockchain( namespace_id )

    # sanity check 
    pay_fee = True
    if user_public_key is not None:
        pay_fee = False

    if user_public_key is None and private_key is None:
        raise Exception("Missing both public and private key")
    
    pubk = None 
    if user_public_key is not None:
        # subsidizing 
        pubk = ECPublicKey( user_public_key )

    else:
        # ordering directly
        pubk = ECPrivateKey( private_key ).public_key()
       
    from_address = pubk.address()
    inputs = get_tx_inputs( blockchain_name, from_address )

    nulldata = build(name, consensus_hash, data_hash=data_hash)
    outputs = make_outputs( nulldata, inputs, from_address, pay_fee=pay_fee )
    return inputs, outputs


def broadcast(name, data_hash, consensus_hash, private_key, blockchain_client, blockchain_broadcaster=None, tx_only=False, user_public_key=None, testset=False):
    """
    Write a name update into the blockchain.
    Returns a JSON object with 'data' set to the nulldata and 'transaction_hash' set to the transaction hash on success.
    """
    
    # sanity check 
    pay_fee = True
    if user_public_key is not None:
        pay_fee = False
        tx_only = True

    if user_public_key is None and private_key is None:
        raise Exception("Missing both public and private key")
    
    if not tx_only and private_key is None:
        raise Exception("Need private key for broadcasting")
    
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

    nulldata = build(name, consensus_hash, data_hash=data_hash, testset=testset)
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
    Parse a payload to get back the name and update hash.
    NOTE: bin_payload excludes the leading three bytes.
    """
    
    if len(bin_payload) != LENGTHS['name_hash'] + LENGTHS['data_hash']:
        log.error("Invalid update length %s" % len(bin_payload))
        return None 

    name_hash_bin = bin_payload[:LENGTHS['name_hash']]
    update_hash_bin = bin_payload[LENGTHS['name_hash']:]
    
    name_hash = hexlify( name_hash_bin )
    update_hash = hexlify( update_hash_bin )
  
    try:
        rc = update_sanity_test( None, name_hash, update_hash )
        if not rc:
            raise Exception("Invalid update data")
    except Exception, e:
        log.error("Invalid update data")
        return None

    return {
        'opcode': 'NAME_UPDATE',
        'name_hash': name_hash,
        'update_hash': update_hash
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


def serialize( nameop ):
    """
    Convert the set of data obtained from parsing the update into a unique string.
    """
    
    return NAME_UPDATE + ":" + str(nameop['name_hash']) + "," + str(nameop['update_hash'])
