# Copyright 2015 HyperBit developers

import enum

from hyperbit import crypto, serialize


class Type(enum.IntEnum):
    getpubkey = 0
    pubkey = 1
    msg = 2
    broadcast = 3


class Getpubkey23(object):
    def __init__(self, ripe):
        self.ripe = ripe

    @classmethod
    def from_bytes(cls, data):
        return cls(data)

    def to_bytes(self):
        return self.ripe


class Getpubkey4(object):
    def __init__(self, tag):
        self.tag = tag

    @classmethod
    def from_bytes(cls, data):
        return cls(data)

    def to_bytes(self):
        return self.tag


class Pubkey2(object):
    def __init__(self, behavior, verkey, enckey):
        self.behavior = behavior
        self.verkey = verkey
        self.enckey = enckey

    @classmethod
    def from_bytes(cls, data):
        return cls(int.from_bytes(data[:4], 'big'), data[4:68], data[68:132])

    def to_bytes(self):
        return self.behavior.to_bytes(4, 'big')  + self.verkey + self.enckey


class Pubkey3(object):
    def __init__(self, behavior, verkey, enckey, trials, extra, signature):
        self.behavior = behavior
        self.verkey = verkey
        self.enckey = enckey
        self.trials = trials
        self.extra = extra
        self.signature = signature

    @classmethod
    def from_bytes(cls, data):
        s = serialize.Deserializer(data)
        behavior = s.uint(4)
        verkey = s.bytes(64)
        enckey = s.bytes(64)
        trials = s.vint()
        extra = s.vint()
        signature = s.vbytes()
        return cls(behavior, verkey, enckey, trials, extra, signature)

    def to_bytes(self):
        s = serialize.Serializer()
        s.uint(self.behavior, 4)
        s.bytes(self.verkey)
        s.bytes(self.enckey)
        s.vint(self.trials)
        s.vint(self.extra)
        s.vbytes(self.signature)
        return s.data


class Pubkey4(object):
    def __init__(self, tag, encrypted):
        assert len(tag) == 32
        self.tag = tag
        self.encrypted = encrypted

    @classmethod
    def from_bytes(cls, data):
        return cls(data[:32], data[32:])

    def to_bytes(self):
        return self.tag + self.encrypted


class Msg1(object):
    def __init__(self, encrypted):
        self.encrypted = encrypted

    @classmethod
    def from_bytes(cls, data):
        return cls(data)

    def to_bytes(self):
        return self.encrypted


class Broadcast4(object):
    def __init__(self, encrypted):
        self.encrypted = encrypted

    @classmethod
    def from_bytes(cls, data):
        return cls(data)

    def to_bytes(self):
        return self.encrypted


class Broadcast5(object):
    def __init__(self, tag, encrypted):
        assert len(tag) == 32
        self.tag = tag
        self.encrypted = encrypted

    @classmethod
    def from_bytes(cls, data):
        return cls(data[:32], data[32:])

    def to_bytes(self):
        return self.tag + self.encrypted


class MsgData(object):
    def __init__(self, addrver, stream, behavior, verkey, enckey, trials, extra, ripe, encoding, message, ack, signature):
        self.addrver = addrver
        self.stream = stream
        self.behavior = behavior
        self.verkey = verkey
        self.enckey = enckey
        self.trials = trials
        self.extra = extra
        self.ripe = ripe
        self.encoding = encoding
        self.message = message
        self.ack = ack
        self.signature = signature

    @classmethod
    def from_bytes(cls, data):
        s = serialize.Deserializer(data)
        addrver = s.vint()
        stream = s.vint()
        behavior = s.uint(4)
        verkey = b'\x04'+s.bytes(64)
        enckey = b'\x04'+s.bytes(64)
        trials = s.vint()
        extra = s.vint()
        ripe = s.bytes(20)
        encoding = s.vint()
        message = s.vbytes()
        ack = s.vbytes()
        signature = s.vbytes()
        return cls(addrver, stream, behavior, verkey, enckey, trials, extra, ripe, encoding, message, ack ,signature)

    def to_bytes(self):
        s = serialize.Serializer()
        s.vint(self.addrver)
        s.vint(self.stream)
        s.uint(self.behavior, 4)
        s.bytes(self.verkey[1:65])
        s.bytes(self.enckey[1:65])
        s.vint(self.trials)
        s.vint(self.extra)
        s.bytes(self.ripe)
        s.vint(self.encoding)
        s.vbytes(self.message)
        s.vbytes(self.ack)
        s.vbytes(self.signature)
        return s.data

    def sign(self, sigkey, object):
        s = serialize.Serializer()
        s.uint(object.expires, 8)
        s.uint(object.type, 4)
        s.vint(object.version)
        s.vint(object.stream)
        s.bytes(object.payload)
        s.vint(self.addrver)
        s.vint(self.stream)
        s.uint(self.behavior, 4)
        s.bytes(self.verkey[1:65])
        s.bytes(self.enckey[1:65])
        s.vint(self.trials)
        s.vint(self.extra)
        s.bytes(self.ripe)
        s.vint(self.encoding)
        s.vbytes(self.message)
        s.vbytes(self.ack)
        self.signature = crypto.sign(sigkey, s.data)

    def verify(self, object):
        s = serialize.Serializer()
        s.uint(object.expires, 8)
        s.uint(object.type, 4)
        s.vint(object.version)
        s.vint(object.stream)
        s.bytes(object.payload)
        s.vint(self.addrver)
        s.vint(self.stream)
        s.uint(self.behavior, 4)
        s.bytes(self.verkey[1:65])
        s.bytes(self.enckey[1:65])
        s.vint(self.trials)
        s.vint(self.extra)
        s.bytes(self.ripe)
        s.vint(self.encoding)
        s.vbytes(self.message)
        s.vbytes(self.ack)
        crypto.verify(self.verkey, s.data, self.signature)

class BroadcastData(object):
    def __init__(self, addrver, stream, behavior, verkey, enckey, trials, extra, encoding, message, signature):
        addrver = addrver
        self.stream = stream
        self.behavior = behavior
        self.verkey = verkey
        self.enckey = enckey
        self.trials = trials
        self.extra = extra
        self.encoding = encoding
        self.message = message
        self.signature = signature

    @classmethod
    def from_bytes(cls, data):
        s = serialize.Deserializer(data)
        addrver = s.vint()
        stream = s.vint()
        behavior = s.uint(4)
        verkey = s.bytes(64)
        enckey = s.bytes(64)
        trials = s.vint()
        extra = s.vint()
        encoding = s.vint()
        message = s.vbytes()
        signature = s.vbytes()
        return cls(addrver, stream, behavior, verkey, enckey, trials, extra, encoding, message, signature)
