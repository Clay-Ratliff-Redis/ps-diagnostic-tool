from concurrent.futures import ThreadPoolExecutor, wait
from threading import Lock

from healthcheck.common_funcs import exec_cmd
from healthcheck.printer_funcs import print_msg, print_success, print_error

SSH_OPTS = ['-o', 'ControlMaster=auto',
            '-o', 'ControlPersist=60s',
            '-o', 'StrictHostKeyChecking=no',
            '-o', 'UserKnownHostsFile=/dev/null',
            '-o', 'IdentitiesOnly=yes',
            '-o', 'AddKeysToAgent=no']


class RemoteExecutor(object):
    """
    Remote Executor class.
    """
    _instance = None

    def __init__(self, _config):
        """
        :param _config: The parsed configuration.
        """
        self.targets = []
        self.ssh_user = None
        self.ssh_key = None
        self.k8s_ns = None
        self.k8s_container = 'redis-enterprise-node'
        self.mode = None

        if 'ssh' in _config:
            self.targets = list(map(lambda x: x.strip(), _config['ssh']['hosts'].split(',')))
            self.ssh_user = _config['ssh']['user']
            self.ssh_key = _config['ssh']['key']
            self.mode = 'ssh'
        elif 'docker' in _config:
            self.targets = list(map(lambda x: x.strip(), _config['docker']['containers'].split(',')))
            self.mode = 'docker'
        elif 'k8s' in _config:
            self.targets = list(map(lambda x: x.strip(), _config['k8s']['pods'].split(',')))
            self.k8s_ns = _config['k8s']['namespace']
            self.mode = 'k8s'
        else:
            raise ValueError('no valid remote executor found')

        self.addrs = {}
        self.locks = {}
        self.cache = {}
        self.connected = None

    @classmethod
    def inst(cls, _config):
        """
        Get singleton instance.

        :param _config: A parsed configuration.
        :return: The RemoteExecutor singleton.
        """
        if not cls._instance:
            cls._instance = RemoteExecutor(_config)

        return cls._instance

    def check_connection(self):
        """
        Check SSH connection.
        """
        if self.connected is not None:
            return

        print_msg(f'checking {self.mode} connections ...')
        for target in self.targets:
            try:
                self.exec_uni('pwd', target, True)
                print_success(f'- successfully connected to {target}')
                self.connected = True
            except Exception as e:
                print_error(f'could not connect to host {target}:', e)
                self.connected = False
        print_msg('')

    def get_addr(self, _hostname):
        """
        Get internal address of node.

        :param _hostname: The hostname of the node.
        :return: The internal address.
        """
        return self.get_addrs()[_hostname]

    def get_addrs(self):
        """
        Get internal addresses of each node.

        :return: The internal addresses.
        """
        if not self.addrs:
            self.addrs = {future.target: future.result().split()[0] for future in self.exec_broad('hostname -I')}

        return self.addrs

    def get_targets(self):
        """
        Get targets.

        :return: A list of targets.
        """
        return self.targets

    def exec_uni(self, _cmd, _target, _su=False):
        """
        Execute a remote command.

        :param _cmd: The command to execute.
        :param _target: The remote machine.
        :param _su: Run as superuser.
        :return: The result.
        :raise Exception: If an error occurred.
        """
        return self._exec(_cmd, _target, _su)

    def exec_multi(self, _cmd_targets, _su=False):
        """
        Execute multiple remote commands.

        :param _cmd_targets: A list of (command, target).
        :param _su: Run as superuser.
        :return: The result.
        :raise Exception: If an error occurred.
        """
        with ThreadPoolExecutor(max_workers=len(_cmd_targets)) as e:
            futures = []
            for cmd, target in _cmd_targets:
                future = e.submit(self._exec, cmd, target, _su)
                future.target = target
                future.cmd = cmd
                futures.append(future)
            done, undone = wait(futures)
            assert not undone

            return done

    def exec_broad(self, _cmd, _su=False):
        """
        Execute a remote command on all targets.

        :param _cmd: The command to execute.
        :param _su: Run as superuser.
        :return: The results.
        :raise Exception: If an error occurred.
        """
        with ThreadPoolExecutor(max_workers=len(self.targets)) as e:
            futures = []
            for target in self.targets:
                future = e.submit(self.exec_uni, _cmd, target, _su)
                future.target = target
                future.cmd = _cmd
                futures.append(future)
            done, undone = wait(futures)
            assert not undone

            return done

    def _exec(self, _cmd, _target, _su=False):
        """
        Execute a remote command.

        :param _cmd: The command to execute.
        :param _target: The remote machine.
        :param _su: Run as superuser.
        :return: The response.
        :raise Exception: If an error occurred.
        """
        # lookup from cache
        if _target in self.cache and _cmd in self.cache[_target]:
            return self.cache[_target][_cmd]

        # build command
        cmd = self._build_cmd(_target, _cmd, _su)

        # create lock if not existent
        if _target not in self.locks:
            self.locks[_target] = Lock()

        # execute command
        with self.locks[_target]:
            rsp = exec_cmd(cmd)

        # put into cache
        if _target not in self.cache:
            self.cache[_target] = {}
        self.cache[_target][_cmd] = rsp

        return rsp

    def _build_cmd(self, _target, _cmd, _su):
        """
        Build a remote command.

        :param _target: The target machine.
        :param _cmd: The command to execute.
        :param _su: Run as superuser.
        :return: The response.
        :raise Exception: If an error occurred.
        """
        if self.mode == 'docker':
            parts = ['docker', 'exec']
            if _su:
                parts.extend(['--user', 'root'])
            parts.extend([_target, _cmd])
        elif self.mode == 'k8s':
            parts = ['kubectl', 'exec', _target,
                     '--container', self.k8s_container,
                     '--namespace', self.k8s_ns,
                     '--', _cmd]
        elif self.mode == 'ssh':
            parts = ['ssh']
            parts.extend(SSH_OPTS)
            if self.ssh_key:
                parts.append('-i {}'.format(self.ssh_key))
            if self.ssh_user:
                parts.append('{}@{}'.format(self.ssh_user, _target))
            else:
                parts.append(_target)
            if _su:
                _cmd = 'sudo ' + _cmd
            parts.append(f'''-C '{_cmd}' ''')
        else:
            raise Exception('unknown REX mode')

        return ' '.join(parts)
