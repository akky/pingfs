import os,sys,socket,struct,select,time,binascii

ping_count = 0
ping_bandwidth = 0
server_list = ['www.google.com','172.16.2.1','10.44.0.1']

def select_server(max_timeout=2):
	server = ''
	min_delay = max_timeout * 1000 # seconds -> ms
	for x in server_list:
		delay = min_delay + 1
		try: delay = single_ping(x,max_timeout)
		finally:
			if delay != None and delay < min_delay:
				min_delay = delay
				server = x
	print 'selected: %s (%.02fms)'%(server,min_delay*1000)
	return server

def carry_add(a, b):
	c = a + b
	return (c & 0xFFFF) + (c >> 16)

def checksum(msg):
	s = 0
	if len(msg)%2: # pad with NULL
		msg = msg + '%c'%0
	for i in range(0, len(msg)/2*2, 2):
		w = ord(msg[i]) + (ord(msg[i+1]) << 8)
		s = carry_add(s, w)
	return ~s & 0xFFFF

def build_ping(ID, data):
	if ID == 0: raise Exception('Invalid BlockID (0): many servers will corrupt ID=0 ICMP messages')

	data = str(data) # string type, like the packed result

	# Header is type (8), code (8), checksum (16), id (16), sequence (16)
	icmp_type		= 8 # ICMP_ECHO_REQUEST
	icmp_code		= 0 # Can be anything, but reply MUST be 0
	icmp_checksum		= 0 # 0 for initial checksum calculation
#	icmp_id			= (ID >> 16) & 0xFFFF
#	icmp_sequence		= (ID <<  0) & 0xFFFF
	block_id		= ID # append id & seq for 4-byte identifier

	header = struct.pack("bbHL", icmp_type, icmp_code, icmp_checksum, block_id)
	icmp_checksum = checksum(header+data)
	header = struct.pack("bbHL", icmp_type, icmp_code, icmp_checksum, block_id)

	# Return built ICMP message
	return header+data

def build_socket():
	icmp = socket.getprotobyname("icmp")
	try:
		icmp_socket = socket.socket(socket.AF_INET, socket.SOCK_RAW, icmp)
	except socket.error, (errno, msg):
		if errno == 1: # Operation not permitted
			msg = msg + (" (ICMP messages can only be sent from processes running as root)")
			raise socket.error(msg)
		raise # raise the original error
	return icmp_socket

def time_ping(d_socket, d_addr, ID=1):
	data = struct.pack("d",time.time())
	return data_ping(d_socket, d_addr, ID, data)

def data_ping(d_socket, d_addr, ID, data):
	global ping_count, ping_bandwidth
	packet = build_ping(ID,data)
	d_addr = socket.gethostbyname(d_addr)
	d_socket.sendto(packet, (d_addr, 1))
	if 1:
		ping_count = ping_count + 1
		ping_bandwidth = ping_bandwidth + len(packet)

def parse_ip(packet):
	if len(packet) < 20: return None
	(verlen,ID,flags,frag,ttl,protocol,csum,src,dst) = struct.unpack('!B3xH4BHLL',packet[:20])
	ip = dict(  version= verlen >> 4,
				length=  4*(verlen & 0xF),
				ID=      ID,
				flags=   flags >> 5,
				fragment=((flags & 0x1F)+frag),
				ttl=     ttl,
				protocol=protocol,
				checksum=csum,
				src=     src,
				dst=     dst)
	return ip

def parse_icmp(packet,validate):
	if len(packet) < 8: return None
	(type, code, csum, block_id) = struct.unpack('bbHL', packet[:8])
	icmp = dict(type=type,
				code=code,
				checksum=csum, # calculated big-endian
				block_id=block_id)

	if validate:
		t_header = struct.pack('bbHL',type,code,0,block_id)
		t_csum = checksum(t_header+packet[8:])
		icmp['valid'] = (t_csum == csum)
		
	return icmp

def parse_ping(packet,validate=False):
	if len(packet) < 28: return None
	ip = parse_ip(packet)
	if not ip:                                return None
	if ip['protocol'] != socket.IPPROTO_ICMP: return None # ICMP
	if ip['version'] != socket.IPPROTO_IPIP:  return None # IPv4
	if ip['length']+8 > len(packet):          return None # invalid ICMP header

	packet = packet[ip['length']:]
	icmp = parse_icmp(packet,validate)
	if not icmp:                              return None
	if icmp['type'] != 0:                     return None # not an Echo Reply packet
	if icmp['code'] != 0:                     return None # not a valid Echo Reply packet
	if validate and icmp['valid'] != True:    return None # invalid ICMP checksum

	payload = packet[8:]
	return dict(ip=ip,icmp=icmp,payload=payload)
	

def recv_ping(d_socket, timeout, validate=False):
	d_socket.settimeout(timeout)
	data,addr = d_socket.recvfrom(65536)
	parsed = parse_ping(data,validate)
	if not parsed: return None
	parsed['ID']=parsed['icmp']['block_id']
	parsed['address']=addr
	parsed['raw']=data
	return parsed

def read_ping(d_socket, timeout):
	start = time.time()
	while time.time() - start < timeout:
		msg = recv_ping(d_socket,timeout)
		if msg: return msg
	return None

def receive_ping(my_socket, ID, timeout):
        timeLeft = timeout
        while True:
                startedSelect = time.time()
                whatReady = select.select([my_socket], [], [], timeLeft)
                if whatReady[0] == []: # Timeout
                        return
        
                timeReceived = time.time()
                howLongInSelect = (timeReceived - startedSelect)
                recPacket, addr = my_socket.recvfrom(1024)
                icmpHeader = recPacket[20:28]
                type, code, checksum, packetID = struct.unpack("bbHL", icmpHeader)
                if packetID == ID:
                        bytesInDouble = struct.calcsize("d")
                        timeSent = struct.unpack("d", recPacket[28:28 + bytesInDouble])[0]
                        return timeReceived - timeSent
        
                timeLeft = timeLeft - howLongInSelect
                if timeLeft <= 0:
                        return 0

def single_ping(dest_addr, timeout):
	my_socket = build_socket()
	my_ID = os.getpid() & 0xFFFF

	time_ping(my_socket, dest_addr, my_ID)
	delay = receive_ping(my_socket, my_ID, timeout)
	my_socket.close()
	return delay

def verbose_ping(dest_addr, timeout = 2, count = 4):
	for i in xrange(count):
		print "ping %s..." % dest_addr,
		try:
			delay  =  single_ping(dest_addr, timeout)
		except socket.gaierror, e:
			print "failed. (socket error: '%s')" % e[1]
			break

		if delay  ==  None:
			print "failed. (timeout within %ssec.)" % timeout
		else:
			delay  =  delay * 1000
			print "get ping in %0.4fms" % delay
	print

def humanize_bytes(bytes, precision=2):
	# by Doug Latornell
	# http://code.activestate.com/recipes/577081-humanized-representation-of-a-number-of-bytes/
	abbrevs = (
		(1<<50L, 'PB'),
		(1<<40L, 'TB'),
		(1<<30L, 'GB'),
		(1<<20L, 'MB'),
		(1<<10L, 'kB'),
		(1, 'bytes')
	)
	if bytes == 1: return '1 byte'
	for factor, suffix in abbrevs:
		if bytes >= factor: break
	return '%.*f %s' % (precision, float(bytes) / factor, suffix)


import os, pwd, grp

def drop_privileges(uid_name='nobody', gid_name='nogroup'):
	# by Tamas
	# http://stackoverflow.com/questions/2699907/dropping-root-permissions-in-python
	if os.getuid() != 0: return

	try:
		# Get the uid/gid from the name
		running_uid = 1000#pwd.getpwnam(uid_name).pw_uid
		running_gid = 1000#grp.getgrnam(gid_name).gr_gid

		# Remove group privileges
		os.setgroups([])

		# Try setting the new uid/gid
		os.setgid(running_gid)
		os.setuid(running_uid)

		# Ensure a very conservative umask
		old_umask = os.umask(077)
		print 'dropped permissions'
	except:
		raise OSError('ping::drop_privileges: failed to drop root privs')

if __name__ == '__main__':
	server = select_server(2)
	
	if 1:
		verbose_ping(server)
	else:
		s = build_socket()
		drop_privileges()
		print 'sending 100 pings...'
		for x in range(1,100):
			data_ping(s,server,x,struct.pack('d',x))
			print 'ping cycled...'
			recv_ping(s,1)
		print '100 pings sent'

