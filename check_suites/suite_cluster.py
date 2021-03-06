import re

from healthcheck.check_suites.base_suite import BaseCheckSuite
from healthcheck.common_funcs import calc_usage, GB, to_gb, to_kops, to_percent


class Cluster(BaseCheckSuite):
    """
    Check configuration, status and usage of the cluster.
    """

    def check_cluster_config_001(self, _params):
        """CC-001: Check cluster sizing.

        Calls '/v1/nodes' from API and compares values with passed paramters.
        If no parameters were passed, only outputs found values.

        Remedy: Upgradinge your nodes.

        :param _params: A dict with cluster sizing values to compare to, see 'parameter_maps/cluster/check_sizing' for examples.
        :returns: result
        """
        number_of_nodes = self.api().get_number_of_values('nodes')
        number_of_cores = self.api().get_sum_of_values('nodes', 'cores')
        total_memory = self.api().get_sum_of_values('nodes', 'total_memory')
        epehemeral_storage_size = self.api().get_sum_of_values('nodes', 'ephemeral_storage_size')
        persistent_storage_size = self.api().get_sum_of_values('nodes', 'persistent_storage_size')

        if not _params:
            info = {'number of nodes': str(number_of_nodes),
                    'number of cores': str(number_of_cores),
                    'total memory': '{} GB'.format(to_gb(total_memory)),
                    'ephemeral storage size': '{} GB'.format(to_gb(epehemeral_storage_size)),
                    'persistent storage size': '{} GB'.format(to_gb(persistent_storage_size))}

            return None, info, "CC-001: Get cluster sizing."

        info = {}
        if number_of_nodes >= _params['min_nodes'] and number_of_nodes % 2 != 0:
            info['number of nodes'] += ' (min: {})'.format(_params['min_nodes'])

        if number_of_cores >= _params['min_cores']:
            info['number of cores'] += ' (min: {})'.format(_params['min_cores'])

        if total_memory >= _params['min_memory_GB'] * GB:
            info['total memory'] += ' (min: {} GB)'.format(_params['min_memory_GB'])

        if epehemeral_storage_size >= _params['min_ephemeral_storage_GB'] * GB:
            info['ephemeral storage size'] += ' (min: {} GB)'.format(_params['min_ephemeral_storage_GB'])

        if persistent_storage_size >= _params['min_persistent_storage_GB'] * GB:
            info['persistent storage size'] += ' (min: {} GB)'.format(_params['min_persistent_storage_GB'])

        return not bool(info), info

    def check_cluster_config_002(self, _params):
        """CC-002: Get master node.

        Executes `rladmin status` on one of the cluster nodes and greps for the master node.
        Outputs UID, internal and external address.

        :param _params: None
        :returns: result
        """
        install_dir = self.get_remote_env_var('installdir')
        rsp = self.rex().exec_uni(f'{install_dir}/bin/rladmin status', self.rex().get_targets()[0], True)
        found = re.search(r'(^\*?node:\d+\s+master.*$)', rsp, re.MULTILINE)
        parts = re.split(r'\s+', found.group(1))

        return None, {'uid': self.api().get_uid(parts[2]), 'address': parts[2], 'external address': parts[3]}

    def check_cluster_config_003(self, _params):
        """CC-003: Get shards distribution.

        Calls '/v1/cluster/shards' and counts shards per node.

        :param _params: None
        :return:  result
        """
        info = {}
        for shard in self.api().get('shards'):
            node = 'node:{}'.format(shard['node_uid'])
            if node not in info:
                info[node] = {'master': 0, 'slave': 0}
            info[node][shard['role']] += 1

        return None, info

    def check_cluster_config_004(self, _params):
        """CC-004: Check license.

        Calls '/v1/license' from API and compares the shards limit with actual shards count and checks expired field.

        Remedy: Update your license.

        :param _params: None
        :returns: result
        """
        number_of_shards = self.api().get_number_of_values('shards')
        _license = self.api().get('license')
        expired = _license['expired']
        if 'shards_limit' in _license:
            shards_limit = int(_license['shards_limit'])
        else:
            match = re.search(r'Shards limit : (\d+)\n', self.api().get('license')['license'], re.MULTILINE | re.DOTALL)
            shards_limit = int(match.group(1))

        result = shards_limit >= number_of_shards and not expired
        info = {'shards limit': shards_limit, 'number of shards': number_of_shards, 'expired': expired,
                'expires': _license['expiration_date']}

        return result, info

    def check_cluser_config_005(self, _params):
        """CC-005: Check min TLS versions.

        Calls '/v1/cluster' and checks 'min_control_tls_version' and 'min_data_tls_version' against '1.2'.

        Remedy: Use `rladmin cluster config` to set 'min_control_tls_version' and 'min_data_tls_version' to '1.2'.

        :param _params: None
        :return: result
        """
        cluster = self.api().get('cluster')
        min_control_tls_version = cluster.get('min_control_TLS_version')
        min_data_tls_version = cluster.get('min_data_TLS_version')

        return min_control_tls_version == '1.2' and min_data_tls_version == '1.2', {
            'min control TLS version': min_control_tls_version,
            'min data TLS version': min_data_tls_version}

    def check_cluster_status_001(self, _params):
        """CS-001: Check cluster health.

        Calls '/v1/cluster/check' from API and outputs the result.

        Remedy: Investigate the failed node, i.e. run `rladmin status`, grep log files for errors, etc.

        :param _params: None
        :returns: result
        """
        result = self.api().get('cluster/check')

        return result['cluster_test_result'], result

    def check_cluster_status_002(self, _params):
        """CS-002: Check cluster shards.

        Calls '/v1/shards' from API and executes `shard-cli <UID> PING` for every shard UID on one of the cluster nodes.
        Collects the responses and compares it against 'PONG'.

        Remedy: Investigate the failed shard, i.e. grep log files for errors.

        :param _params: None
        :returns: result
        """
        info = {}
        install_dir = self.get_remote_env_var('installdir')
        for shard in self.api().get('shards'):
            ping_rsp = self.rex().exec_uni(f'{install_dir}/bin/shard-cli {shard["uid"]} PING', self.rex().get_targets()[0], True)
            if ping_rsp != 'PONG' or shard['status'] != 'active' or shard['detailed_status'] != 'ok':
                info[f'shard:{shard["uid"]}'] = shard

        return not info, info if info else {'OK': 'all'}

    def check_cluster_status_003(self, _params):
        """CS-003: Check if `rladmin status` has errors.

        Executes `rladmin status | grep -v endpoint | grep node` on one of the cluster nodes.
        Collects output and compares against 'OK'.

        Remedy: Investigate the failed node, i.e. grep log files for errors.

        :param _params: None
        :returns: result
        """
        install_dir = self.get_remote_env_var('installdir')
        rsp = self.rex().exec_uni(f'{install_dir}/bin/rladmin status | grep -v endpoint | grep node',
                                  self.rex().get_targets()[0], True)
        not_ok = re.findall(r'^((?!OK).)*$', rsp, re.MULTILINE)

        return len(not_ok) == 0, {'not OK': len(not_ok)} if not_ok else {'OK': 'all'}

    def check_cluster_status_004(self, _params):
        """CS-004: Check cluster alerts.

        Calls '/v1/cluster/alerts' from API and outputs triggered alerts.

        Remedy: Investigate triggered alerts by checking log files.

        :param _params: None
        :returns: result
        """
        alerts = self.api().get('cluster/alerts')
        enableds = list(filter(lambda x: x[1]['state'], alerts.items()))

        return not enableds, dict(enableds)

    def check_cluster_usage_001(self, _params):
        """CU-001: Get throughput of cluster.

        Calls '/v1/cluster/stats' from API and calculates min/avg/max/dev of 'total_req' (total requests per second).

        :param _params: None
        :returns: result
        """
        info = {}
        stats = self.api().get('cluster/stats')

        minimum, average, maximum, std_dev = calc_usage(stats['intervals'], 'total_req')

        info['min'] = '{} Kops'.format(to_kops(minimum))
        info['avg'] = '{} Kops'.format(to_kops(average))
        info['max'] = '{} Kops'.format(to_kops(maximum))
        info['dev'] = '{} Kops'.format(to_kops(std_dev))

        return None, info

    def check_cluster_usage_002(self, _params):
        """CU-002: Get memory usage of cluster.

        Calls '/v1/cluster/stats' from API and calculates min/avg/max/dev of 'total_memory' - 'free_memory' (used memory).

        :param _params: None
        :returns: result
        """
        info = {}
        stats = self.api().get('cluster/stats')

        minimum, average, maximum, std_dev = calc_usage(stats['intervals'], 'free_memory')

        total_mem = self.api().get_sum_of_values('nodes', 'total_memory')

        info['min'] = '{} GB ({} %)'.format(to_gb(total_mem - maximum), to_percent((100 / total_mem) * (total_mem - maximum)))
        info['avg'] = '{} GB ({} %)'.format(to_gb(total_mem - average), to_percent((100 / total_mem) * (total_mem - average)))
        info['max'] = '{} GB ({} %)'.format(to_gb(total_mem - minimum), to_percent((100 / total_mem) * (total_mem - minimum)))
        info['dev'] = '{} GB ({} %)'.format(to_gb(std_dev), to_percent((100 / total_mem) * std_dev))

        return None, info

    def check_cluster_usage_003(self, _params):
        """CU-003: Get ephemeral storage usage of cluster.

        Calls '/v1/cluster/stats' from API and calculates
        min/avg/max/dev of 'ephemeral_storage_size' - 'ephemeral_storage_avail' (used ephemeral storage).

        :param _params: None
        :returns: result
        """
        info = {}
        stats = self.api().get('cluster/stats')

        minimum, average, maximum, std_dev = calc_usage(stats['intervals'], 'ephemeral_storage_avail')

        total_size = self.api().get_sum_of_values(f'nodes', 'ephemeral_storage_size')

        info['min'] = '{} GB ({} %)'.format(to_gb(total_size - maximum), to_percent((100 / total_size) * (total_size - maximum)))
        info['avg'] = '{} GB ({} %)'.format(to_gb(total_size - average), to_percent((100 / total_size) * (total_size - average)))
        info['max'] = '{} GB ({} %)'.format(to_gb(total_size - minimum), to_percent((100 / total_size) * (total_size - minimum)))
        info['dev'] = '{} GB ({} %)'.format(to_gb(std_dev), to_percent((100 / total_size) * std_dev))

        return None, info

    def check_cluster_usage_004(self, _params):
        """CU-004: Get persistent storage usage of cluster.

        Calls '/v1/cluster/stats' from API and calculates
        min/avg/max/dev of 'persistent_storage_size' - 'persistent_storage_avail' (used persistent storage).

        :param _params: None
        :returns: result
        """
        info = {}
        stats = self.api().get('cluster/stats')

        minimum, average, maximum, std_dev = calc_usage(stats['intervals'], 'persistent_storage_avail')

        total_size = self.api().get_sum_of_values(f'nodes', 'persistent_storage_size')

        info['min'] = '{} GB ({} %)'.format(to_gb(total_size - maximum), to_percent((100 / total_size) * (total_size - maximum)))
        info['avg'] = '{} GB ({} %)'.format(to_gb(total_size - average), to_percent((100 / total_size) * (total_size - average)))
        info['max'] = '{} GB ({} %)'.format(to_gb(total_size - minimum), to_percent((100 / total_size) * (total_size - minimum)))
        info['dev'] = '{} GB ({} %)'.format(to_gb(std_dev), to_percent((100 / total_size) * std_dev))

        return None, info

    def check_cluster_usage_005(self, _params):
        """CU-005: Get network traffic usage of cluster.

        Calls '/v1/cluster/stats' from API and calculates min/avg/max/dev of 'ingress_bytes' and 'egress_bytes'.

        :param _params:
        :return:
        """
        info = {}
        stats = self.api().get('cluster/stats')

        minimum, average, maximum, std_dev = calc_usage(stats['intervals'], 'ingress_bytes')
        info['ingress'] = {
            'min': '{} GB/s'.format(to_gb(minimum)),
            'avg': '{} GB/s'.format(to_gb(average)),
            'max': '{} GB/s'.format(to_gb(maximum)),
            'dev': '{} GB/s'.format(to_gb(std_dev)),
        }
        minimum, average, maximum, std_dev = calc_usage(stats['intervals'], 'egress_bytes')
        info['egress'] = {
            'min': '{} GB/s'.format(to_gb(minimum)),
            'avg': '{} GB/s'.format(to_gb(average)),
            'max': '{} GB/s'.format(to_gb(maximum)),
            'dev': '{} GB/s'.format(to_gb(std_dev)),
        }

        return None, info
