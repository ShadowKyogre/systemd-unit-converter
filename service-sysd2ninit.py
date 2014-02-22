import configparser
import argparse
import glob
import shlex
from os import path
import os
from itertools import chain
from collections import OrderedDict

#Special Dictionary to deal with how systemd unit files are structured

class SystemdODict(OrderedDict):
	PILE_ME_UP=('Requires','RequiresOverrideable','Requisite',
			 'Wants','BindsTo', 'PartOf','Conflicts','Before',
			 'After','OnFailure','PropagatesReloadTo','ReloadPropagatedFrom',
			 'JoinsNamespaceOf','Alias','WantedBy','RequiredBy','Also',
			 'ReadWriteDirectories', 'ReadOnlyDirectories', 'InaccessibleDirectories',
			 'SupplementaryGroups')
	UNNEEDED_DEPS=['network.target','network-online.target','umount.target','basic.target']
	def __setitem__(self, key, value):
		if isinstance(value, list) and key in self:
			self[key].extend(value)
		else:
			if key in self.PILE_ME_UP:
				value=value.split(' ')
			#print(key,value)
			super(OrderedDict, self).__setitem__(key, value)

def ninit_service(cfg,f):
	#we'll need a way to make a service maker based on templates
	service_name=path.splitext(path.basename(f))[0]
	newf = path.join(path.abspath(args.output),service_name)
	if not(path.exists(newf)):
		os.makedirs(newf)
	
	#handle anything in in [Unit] first
	
	## Let's create a short README to help preserve documentation
	
	README=['# {}'.format(service_name)]
	if 'Description' in cfg['Unit']:
		README.extend(cfg['Unit']['Description'])
		README.append(' ')
	if 'Documentation' in cfg['Unit']:
		README.extend(['Unit']['Documentation'])
	
	if len(README) > 1:
		readme_file = open(path.join(newf,'README'),'w')
		readme_file.write('\n'.join(README))
		readme_file.close()
		
	## End README
	
	## Handle dependencies
	# handle Hard dependencies first
	depends=[]
	if 'Requires' in cfg['Unit']:
		depends.extend(cfg['Unit']['Requires'])
	if 'Wants' in cfg['Unit']:
		depends.extend(cfg['Unit']['Wants'])
	else:
		#it's probably specified in the directories
		syswants=os.path.join('/usr/lib/systemd/system',service_name,'.wants','*')
		etcwants=os.path.join('/etc/systemd/system',service_name,'.wants','*')
		depends.extend([path.splitext(path.basename(n))[0] for n in glob.iglob(syswants)])
		depends.extend([path.splitext(path.basename(n))[0] for n in glob.iglob(etcwants)])
	if 'Requisite' in cfg['Unit']:
		depends.extend(cfg['Unit']['Requisite']) #how does ninit handle failing dependencies?
	if 'BindsTo' in cfg['Unit']:
		depends.extend(cfg['Unit']['BindsTo'])
		#be sure to tell the script later to write a special run?
	if 'PartOf' in cfg['Unit']:
		depends.extend(cfg['Unit']['PartOf'])

	## Check any Conditionals and write those to a special setup file
	setup=[]
	## Once ExecStartPre things are gathered too

	if 'Service' in cfg:
		#then in [Service]
		sertype=cfg['Service'].get('Type', [''])[0]
		if not sertype and 'BusName' not in cfg['Service']:
			sertype='simple'
		elif not sertype and 'BusName' in cfg['Service']:
			sertype='dbus'

		if sertype=='dbus':
			depends.append('dbus')
		elif sertype=='oneshot':
			sync_file=open(path.join(newf,'sync'),'w')
			sync_file.write('')
			sync_file.close()
		
		## We're done collecting dependencies, let's write the depends file
		## also, add any mentioned files that aren't part of the conversion
		## to be processed later
		#print(depends)
		if len(depends) > 1:
			#separate these into before and after
			
			#now remove anything silly, like network.target
			for d in SystemdODict.UNNEEDED_DEPS:
				if d in depends: depends.remove(d)
			
			#if they're specified in after, write them to depends
			depends_file= open(path.join(newf,'depends'),'w')
			depends_file.write('\n'.join([path.splitext(i)[0] for i in depends]))
			
			#if they're specified in before, write them to a special end file
			depends_file.close()
		
		## End Handle dependencies
		
		if cfg['Service'].get('PIDFile',[''])[0] != '':
			pidfile=open(path.join(newf,'pidfile'),'w')
			pidfile.write(cfg['Service']['PIDFile'])
			pidfile.close()
		
		#support multiple ExecStart in the case of oneshots
		cmd=cfg['Service'].get('ExecStart',[''])
		#strip - and @ at the beginning
		cmd_length=len(cmd)
		if (';' in cmd and cmd_length == 1) or cmd_length > 1:
			import stat
			runpath=path.join(newf,'run')
			run_file=open(runpath,'w')
			run_file.write('#!/bin/sh\n')
			run_file.write('\n'.join(cmd)) #write bindto stuff here?
			run_file.close()
			st=os.stat(runpath)
			os.chmod(runpath,st.st_mode|stat.S_IXUSR|stat.S_IXGRP|stat.S_IXOTH)
		else:
			cmd_parts=shlex.split(cmd[0])
			runpath=path.join(newf,'run')
			if path.exists(runpath):
				os.remove(runpath)
			os.symlink(cmd_parts[0],runpath)
			params=open(path.join(newf,'params'),'w')
			params.write('\n'.join(cmd_parts[1:]))
			params.close()
		
		if 'ExecStartPre' in cfg['Service']:
			setup.extend(cfg['Service']['ExecStartPre'])
		end=[]
		if 'ExecStartPost' in cfg['Service']:
			end.extend(cfg['Service']['ExecStartPost'])
		#handle ExecStop and ExecStopPost how? I think by writing that special run file, but idk
		if 'EnvironmentFile' in cfg['Service']:
			import shutil
			shutil.copy(cfg['Service']['EnvironmentFile'][0],path.join(newf,'environ'))
		elif 'Environment' in cfg['Service']:
			environ=cfg['Service']['Environment'][0]
			if 'WorkingDirectory' in cfg['Service']:
				environ.append('PWD={}'.format(cfg['Service']['WorkingDirectory'][0]))
			environ='\n'.join(shlex.split(environ))
			environ_file=open(path.join(newf,'environ'),'w')
			environ_file.write(environ)
			environ_file.close()
		
		if 'User' in cfg['Service']:
			try:
				uid=int(cfg['Service']['User'][0])
			except ValueError as e:
				from pwd import getpwnam
				uid=getpwnam(cfg['Service']['User'][0]).pw_uid
			if 'Group' in cfg['Service']:
				try:
					gid=int(cfg['Service']['Group'][0])
				except ValueError as e:
					from grp import getgrnam
					gid=getgrnam(cfg['Service']['Group'][0]).gr_gid
			else:
				from pw import getpwuid
				gid=getpwuid(uid).gr_gid
			more_gids=[]
			if 'SupplementaryGroups' in cfg['Service']:
				for i in cfg['Service']['SupplementaryGroups']:
					try:
						ggid=int(i)
					except ValueError as e:
						from grp import getgrnam
						ggid=getgrnam(i).gr_gid
					more_gids.append(ggid)
			uid_file=open(path.join(newf,'uid'),'w')
			uid_file.write("{}:{}{}".format(uid,gid,
						":{}".format(":".join(more_gids)) \
						if len(more_gids) > 0 else ""))
			uid_file.close()
		
		if cfg['Service'].get('Restart',['no'])[0] != 'no':
			respawn_file=open(path.join(newf,'respawn'),'w')
			respawn_file.write('')
			respawn_file.close()
			sleep=cfg['Service']['RestartSec'] if 'RestartSec' in cfg['Service'] else '1'
			#check for time format and change it to just seconds
			sleep_file=open(path.join(newf,'sleep'),'w')
			sleep_file.write(sleep)
			sleep_file.close()

	if len(end) > 0:
		import stat
		endpath=path.join(newf,'end')
		end_file=open(endpath,'w')
		end_file.write('#!/bin/sh\n')
		end_file.write('\n'.join(end)) #write bindto stuff here?
		end_file.close()
		st=os.stat(endpath)
		os.chmod(endpath,st.st_mode|stat.S_IXUSR|stat.S_IXGRP|stat.S_IXOTH)
	
	if len(setup) > 0:
		import stat
		setuppath=path.join(newf,'setup')
		setup_file=open(setuppath,'w')
		setup_file.write('#!/bin/sh\n')
		setup_file.write('\n'.join(setup)) #write bindto stuff here?
		setup_file.close()
		st=os.stat(setuppath)
		os.chmod(setuppath,st.st_mode|stat.S_IXUSR|stat.S_IXGRP|stat.S_IXOTH)

	elif 'Socket' in cfg:
		#we only care about file listen streams
		pass
	elif 'Mount' in cfg:
		#output to args.output/fstab.addons
		pass
	if 'Automount' in cfg:
		#output to args.output/fstab.addons
		#treat this as if defaults and type = auto?
		pass
	elif 'Swap' in cfg:
		#output to args.output/fstab.addons
		pass
	#elif 'Path' in cfg:
	#	pass
	print(f,"->",newf)

parser = argparse.ArgumentParser(description='Convert systemd files to ninit service directories')
parser.add_argument('--output','-o',type=str,
		help="Output the service directory into this folder",default=".")
parser.add_argument('files',metavar='F',type=str,nargs='*',help="Service files to convert")
args = parser.parse_args()

if len(args.files) < 1:
	files = chain(glob.iglob('/usr/lib/systemd/system/*.service'),
			glob.iglob('/usr/lib/systemd/system/*.socket'))
else:
	files=args.files

#/usr/lib/systemd/system/wicd.service
for f in files:
	with open(f, 'r') as service_file:
		cfg = configparser.RawConfigParser(dict_type=SystemdODict)
		cfg.read_file(service_file)
		ninit_service(cfg,f)
