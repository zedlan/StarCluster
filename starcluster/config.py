#!/usr/bin/env python
import os
import sys
import ConfigParser

from starcluster.cluster import Cluster
from starcluster import static 
from starcluster import awsutils 
from starcluster.utils import AttributeDict
from starcluster.templates.config import config_template
from starcluster import exception 

from starcluster.logger import log

def get_easy_s3():
    """
    Factory for EasyS3 class that attempts to load AWS credentials from
    the StarCluster config file. Returns an EasyS3 object if
    successful.
    """
    cfg = StarClusterConfig(); cfg.load()
    return cfg.get_easy_s3()

def get_easy_ec2():
    """
    Factory for EasyEC2 class that attempts to load AWS credentials from
    the StarCluster config file. Returns an EasyEC2 object if
    successful.
    """
    cfg = StarClusterConfig(); cfg.load()
    return cfg.get_easy_ec2()

def get_aws_from_environ():
    """Returns AWS credentials defined in the user's shell
    environment."""
    awscreds = {}
    for key in static.AWS_SETTINGS:
        if os.environ.has_key(key):
            awscreds[key] = os.environ.get(key)
    return awscreds

def get_config(config_file=None, cache=False):
    """Factory for StarClusterConfig object"""
    return StarClusterConfig(config_file, cache)

class StarClusterConfig(object):
    """
    Loads StarCluster configuration settings defined in config_file
    which defaults to ~/.starclustercfg

    Settings are available as follows:

    cfg = StarClusterConfig()
    or
    cfg = StarClusterConfig('/path/to/my/config.cfg')
    cfg.load()
    aws_info = cfg.aws
    cluster_cfg = cfg.clusters['mycluster']
    key_cfg = cfg.keys['gsg-keypair']
    print cluster_cfg
    """

    DEFAULT_CFG_FILE = os.path.join(os.path.expanduser('~'),'.starclustercfg')

    # until i can find a way to query AWS for instance types...
    instance_types = static.INSTANCE_TYPES
    aws_settings = static.AWS_SETTINGS
    cluster_settings = static.CLUSTER_SETTINGS
    key_settings = static.KEY_SETTINGS
    volume_settings = static.EBS_VOLUME_SETTINGS

    def __init__(self, config_file=None, cache=False):
        if config_file:
            if os.path.exists(config_file):
                if os.path.isfile(config_file):
                    self.cfg_file = config_file
                else:
                    log.warn('config %s exists but is not a regular file, defaulting to %s' %
                    (config_file,self.DEFAULT_CFG_FILE))
                    self.cfg_file = self.DEFAULT_CFG_FILE
            else:
                log.warn('config %s does not exist, defaulting to %s' %
                (config_file, self.DEFAULT_CFG_FILE))
                self.cfg_file = self.DEFAULT_CFG_FILE
        else:
            self.cfg_file = self.DEFAULT_CFG_FILE

        self.type_validators = {
            int: self._get_int,
            str: self._get_string,
        }
        self._config = None
        self.aws = AttributeDict()
        self.clusters = AttributeDict()
        self.keys = AttributeDict()
        self.vols = AttributeDict()
        self.cache = cache

    def _get_int(self, config, section, option):
        try:
            opt = config.getint(section,option)
        except (ConfigParser.NoSectionError):
            opt = None
        except (ConfigParser.NoOptionError):
            opt = None
        except (ValueError):
            log.warn("Expected integer value for option %s in %s, not setting option!" % (option,section))
            opt = None
        return opt

    def _get_string(self, config, section, option):
        try:
            opt = config.get(section,option)
        except (ConfigParser.NoSectionError):
            opt = None
        except (ConfigParser.NoOptionError):
            opt = None
        return opt

    @property
    def config(self):
        # TODO: create the template file for them?
        CFG_FILE = self.cfg_file
        #if not os.path.exists(CFG_FILE):
            #print config_template
            #log.info('It appears this is your first time using StarCluster.')
            #log.info('Please create %s using the template above.' % CFG_FILE)
            #sys.exit(1)
        if not self.cache or self._config is None:
            try:
                self._config = ConfigParser.ConfigParser()
                self._config.read(CFG_FILE)
            except ConfigParser.MissingSectionHeaderError,e:
                log.warn('No sections defined in settings file %s' % CFG_FILE)
        return self._config

    def load_settings(self, section_prefix, section_name, settings, store):
        section_key = ' '.join([section_prefix, section_name])
        section_conf = store
        for setting in settings:
            requirements = settings[setting]
            name = setting
            func = self.type_validators.get(requirements[0])
            required = requirements[1];
            default = requirements[2]
            value = func(self.config, section_key, name)
            if value:
                section_conf[name] = value

    def load_defaults(self, settings, store):
        section_conf = store
        for setting in settings:
            name = setting; default = settings[setting][2]
            if section_conf.get(name, None) is None:
                if default:
                    log.warn('No %s setting specified. Defaulting to %s' % (name, default))
                section_conf[name] = default

    def load_extends_variables(self, section_name, store):
        section = store[section_name]
        extends = section['EXTENDS'] = section.get('EXTENDS')
        if extends is None:
            return
        log.debug('%s extends %s' % (section, extends))
        extensions = [section]
        while True:
            extends = section.get('EXTENDS',None)
            if extends:
                try:
                    section = store[extends]
                    extensions.insert(0, section)
                except KeyError,e:
                    log.warn("can't extend non-existent section %s" % extends)
                    break
            else:
                break
        transform = AttributeDict()
        for extension in extensions:
            transform.update(extension)
        store[section_name] = transform

    def load_keypairs(self, section_name, store):
        cluster_section = store
        keyname = cluster_section.get('KEYNAME')
        keypair = self.keys.get(keyname)
        if keypair is None:
            log.warn("keypair %s not defined in config" % keyname)
            return
        cluster_section['KEYNAME'] = keyname
        cluster_section['KEY_LOCATION'] = keypair.get('KEY_LOCATION')

    def load_volumes(self, section_name, store):
        cluster_section = store
        volumes = cluster_section.get('VOLUMES')
        if volumes is None:
            return
        volumes = [vol.strip() for vol in volumes.split(',')]
        vols = AttributeDict()
        for volume in volumes:
            if self.vols.has_key(volume):
                vols[volume] = self.vols.get(volume)
            else:
                log.warn("volume %s not defined in config" % volume)
        cluster_section['VOLUMES'] = vols

    def load(self):
        self.load_settings('aws', 'info', self.aws_settings, self.aws)
        keys = [section.split()[1] for section in self.config.sections() if section.startswith('key')]
        for key in keys:
            self.keys[key] = AttributeDict()
            self.load_settings('key', key, self.key_settings, self.keys[key]) 
        vols = [section.split()[1] for section in self.config.sections() if section.startswith('volume')]
        for vol in vols:
            self.vols[vol] = AttributeDict()
            self.load_settings('volume', vol, self.volume_settings, self.vols[vol])
        clusters = [section.split()[1] for section in self.config.sections() if section.startswith('cluster')]
        for cluster in clusters:
            self.clusters[cluster] = AttributeDict()
            self.load_settings('cluster', cluster, self.cluster_settings,
                               self.clusters[cluster])
        for cluster in clusters:
            self.load_extends_variables(cluster, self.clusters)
            self.load_defaults(self.cluster_settings, self.clusters[cluster])
            self.load_keypairs(cluster, self.clusters[cluster])

        for cluster in clusters:
            self.load_volumes(cluster, self.clusters[cluster])

    def get_aws_credentials(self):
        """Returns AWS credentials defined in the configuration
        file. Defining any of the AWS settings in the environment
        overrides the configuration file."""
        # first override with environment settings if they exist
        self.aws.update(get_aws_from_environ())
        return self.aws

    def get_cluster_names(self):
        return self.clusters

    def get_cluster(self, cluster_name):
        try:
            kwargs = {}
            kwargs.update(**self.aws)
            kwargs.update(self.clusters[cluster_name])
            clust = Cluster(**kwargs)
            return clust
        except KeyError,e:
            raise exception.ClusterDoesNotExist(
                'config for cluster %s does not exist' % cluster_name)

    def get_clusters(self):
        clusters = []
        for cluster in self.clusters:
            clusters.append(self.get_cluster(cluster))
        return clusters

    def get_key(self, keyname):
        try:
            return self.keys[keyname]
        except:
            pass

    def get_easy_s3(self):
        """
        Factory for EasyEC2 class that attempts to load AWS credentials from
        the StarCluster config file. Returns an EasyEC2 object if
        successful.
        """
        s3 = awsutils.EasyS3(self.aws['AWS_ACCESS_KEY_ID'],
                             self.aws['AWS_SECRET_ACCESS_KEY'])
        return s3

    def get_easy_ec2(self):
        """
        Factory for EasyEC2 class that attempts to load AWS credentials from
        the StarCluster config file. Returns an EasyEC2 object if
        successful.
        """
        ec2 = awsutils.EasyEC2(self.aws['AWS_ACCESS_KEY_ID'],
                             self.aws['AWS_SECRET_ACCESS_KEY'])
        return ec2

if __name__ == "__main__":
    from pprint import pprint
    cfg = StarClusterConfig(); cfg.load()
    pprint(cfg.aws)
    pprint(cfg.clusters)
    pprint(cfg.keys)
    pprint(cfg.vols)
