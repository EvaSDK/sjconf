import re, sys, os, errno, time, popen2

from sjconfparts.type import *
from sjconfparts.plugin import *
from sjconfparts.conf import *

class SJConf:

    BASE_COMMENTS           = """
/!\ WARNING /!\
Do not edit this file ! Your modifications will be overwritten at next upgrade.
Instead, add your custom values in local.conf.
"""

    DEFAULT_SJCONF_FILE_NAME = '/etc/smartjog/sjconf.conf'

    def __init__(self, sjconf_file_path = DEFAULT_SJCONF_FILE_NAME, quiet = True, verbose = False):

        self.confs_internal = {'sjconf' : Conf(file_path = sjconf_file_path)}
        self.confs_internal['sjconf'].set_type('conf', 'plugins', 'list')

        self.backup_dir = os.path.realpath(self.confs_internal['sjconf']['conf']['backup_dir'] + '/' + time.strftime('%F-%R:%S', time.localtime()))
        self.etc_dir = os.path.realpath(self.confs_internal['sjconf']['conf']['etc_dir'])
        self.base_dir = os.path.realpath(self.confs_internal['sjconf']['conf']['base_dir'])

        self.confs = {}
        for conf in ('base', 'local'):
            conf_file_path = os.path.realpath(self.base_dir + '/' + conf + '.conf')
            self.confs[conf] = Conf(file_path = conf_file_path)
        self.confs['base'].comments = self.BASE_COMMENTS

        self.temp_file_path = "/tmp/sjconf_tempfile.conf"

        self.files_path = {'plugin' : os.path.realpath(self.confs_internal['sjconf']['conf']['plugins_path']), 'template' : os.path.realpath(self.confs_internal['sjconf']['conf']['templates_path'])}
        self.files_extensions = {'plugin' : ('.py'), 'template' : ('.conf')}

        sys.path.append(self.files_path['plugin'])

        self.plugins = []

        self.quiet = quiet
        self.verbose = verbose

        self.__plugins_load()

    def conf(self):
        conf = Conf(self.confs['base'])
        conf.update(self.confs['local'])
        return conf

    def plugin_conf(self, plugin_name):
        conf = self.conf()
        plugin_conf = Conf()
        for section in conf:
            if re.compile(plugin_name + ':?.*').match(section):
                plugin_conf[section] = conf[section]
        return plugin_conf

    def restart_services(self, services_to_restart):
        already_restart = []
        for plugin in self.plugins:
            if plugin in services_to_restart or 'all' in services_to_restart:
                plugin.restart_all_services()

    def apply_conf_modifications(self, sets = [], delete_keys = [], delete_sections = [], temp = False):
        conf = self.confs['local']
        if sets or delete_keys or delete_sections:
            self.__my_print("########## Scheduled modifications ##############")

            for section in delete_sections:
                if section in conf:
                    del(conf[section])
                self.__my_print('delete section : %s' % (section))

            for section, key in delete_keys:
                if section in conf:
                    if key in conf[section]:
                        del(conf[section][key])
                self.__my_print('delete key     : %s:%s' % (section, key))

            for section, key, value in sets:
                conf.setdefault(section, {})
                conf[section][key] = value
                self.__my_print('set            : %s:%s=%s' % (section, key, value))
            self.__my_print("#################################################\n")

        if temp:
            output_file = self.temp_file_path
        else:
            output_file = conf.file_path
        conf.save(output_file)

    def deploy_conf(self, services_to_restart):
        conf_files = self.__conf_files()
        files_to_backup = self.__files_to_backup() + conf_files
        self.backup_files(files_to_backup)

        try:
            # Write all configuration files
            self.__apply_confs(conf_files)

            # restart services if asked
            if len(services_to_restart) > 0:
                self.restart_services(services_to_restart)
            self.__my_print('')
        except:
            # Something when wrong, restoring backup files
            self.restore_files(files_to_backup)
            if len(services_to_restart) > 0:
                self.restart_services(services_to_restart)
            # And delete backup folder
            self.__delete_backup_dir()
            raise
        # Only archive once everything is OK
        self.__archive_backup()
        self.__my_print('')
        # Delete backup, everything is cool
        self.__delete_backup_dir()

    def file_install(self, file_type, file_to_install, link=False):
        if self.verbose:
           self. __my_print("Installing file: %s" % (file_to_install))
        if os.path.exists(self.files_path[file_type] + '/' + os.path.basename(file_to_install)):
            raise IOError(errno.EEXIST, "file %s already installed" % (file_to_install))
        file_destination_path = self.files_path[file_type] + '/' + os.path.basename(file_to_install)
        if not link:
            if os.path.isdir(file_to_install):
                shutil.copytree(file_to_install, file_destination_path)
            else:
                shutil.copy(file_to_install, file_destination_path)
        else:
            os.symlink(os.path.realpath(file_to_install), file_destination_path)
        if self.verbose:
            self.__my_print("Installed file: %s" % (file_to_install))

    def file_uninstall(self, file_type, file_to_uninstall):
        if self.verbose:
            self.__my_print("Uninstalling file: %s" % (file_to_uninstall))
        file_to_uninstall_path = self.__file_path(file_type, file_to_uninstall)
        if not os.path.islink(file_to_uninstall_path) and os.path.isdir(file_to_uninstall_path):
            shutil.rmtree(file_to_uninstall_path)
        else:
            os.unlink(file_to_uninstall_path)
        if self.verbose:
            self.__my_print("Uninstalled file: %s" % (file_to_uninstall))

    def plugin_enable(self, plugin_to_enable):
        # ensure the plugin in installed
        self.__file_path('plugin', plugin_to_enable)
        plugins_list = self.confs_internal['sjconf']['conf']['plugins_list']
        if plugin_to_enable in plugins_list:
            raise IOError(errno.EEXIST, "plugin %s already enabled" % (plugin_to_enable))
        self.confs_internal['sjconf']['conf']['plugins_list'].append(plugin_to_enable)
        self.confs_internal['sjconf'].save()

    def plugin_disable(self, plugin_to_disable):
        # ensure the plugin in installed
        self.__file_path('plugin', plugin_to_disable)
        try:
            self.confs_internal['sjconf']['conf']['plugins_list'].remove(plugin_to_disable)
        except ValueError:
            raise IOError(errno.ENOENT, "plugin %s not enabled" % (plugin_to_disable))
        self.confs_internal['sjconf'].save()

    def conf_file_install(self, conf_file_to_install):
        conf_file_to_install_name = os.path.basename(conf_file_to_install).replace('.conf', '')
        conf_to_install = Conf(file_path = conf_file_to_install)
        for section in conf_to_install:
            if not re.compile(conf_file_to_install_name + ':?.*').match(section):
                raise KeyError(section + ': All sections should start with \'' + conf_file_to_install_name + '\', optionnally followed by \':<subsection>\'')
            if section in self.confs['base']:
                raise IOError(errno.EEXIST, "config %s is already installed" % (conf_file_to_install_name))
        for section in conf_to_install:
            self.confs['base'][section] = {}
            for key in conf_to_install[section]:
                self.confs['base'][section][key] = conf_to_install[section][key]
        self.confs['base'].save()

    def conf_file_uninstall(self, conf_file_to_uninstall):
        sections_to_delete = []
        conf_file_to_uninstall_name = os.path.basename(conf_file_to_uninstall).replace('.conf', '')
        for section in self.confs['base']:
            if re.compile(conf_file_to_uninstall_name + ':?.*').match(section):
                sections_to_delete.append(section)
        if len(sections_to_delete) == 0:
            raise IOError(errno.ENOENT, "config %s is not installed" % (conf_file_to_uninstall_name))
        for section in sections_to_delete:
            del self.confs['base'][section]
        self.confs['base'].save()

    def __my_print(self, str):
        if not self.quiet: print str

    def __exec_command(self, command, input = ''):
        # Using popen to know program output
        cmd = popen2.Popen3(command, True)
        cmd.tochild.write(input)
        out = cmd.fromchild.read()
        err = cmd.childerr.read()
        exit_value = cmd.wait()

        return out, err, exit_value

    def __plugin_dependencies(self, plugin, plugins_hash):
        plugin_dependencies_hash = {}
        for dependency in plugin.dependencies():
            if not dependency.name in plugins_hash and dependency.optionnal:
                continue
            if not dependency.name in plugins_hash and not dependency.optionnal: # Plugin is not available, find out if it is not installed or not enabled
                try:
                    self.__file_path('plugin', dependency.name) # This will raise an IOError if plugin is not installed
                    raise Plugin.Dependency.NotEnabledError
                except IOError, exception:
                    if hasattr(exception, 'errno') and exception.errno == errno.ENOENT:
                        raise Plugin.Dependency.NotInstalledError
                    else:
                        raise
            dependency.verify(plugins_hash[dependency.name].version())
            plugin_dependencies_hash[dependency.name] = plugins_hash[dependency.name]
        return plugin_dependencies_hash

    def __plugins_load(self):
        for plugin in self.confs_internal['sjconf']['conf']['plugins_list']:
            self.plugins.append(__import__(plugin).Plugin(self, self.plugin_conf(plugin)))
        plugins_hash = {}
        for plugin in self.plugins:
            plugins_hash[plugin.name()] = plugin
        for plugin in self.plugins:
            plugin_dependencies_hash = self.__plugin_dependencies(plugin, plugins_hash)
            if len(plugin_dependencies_hash) > 0: 
                plugin.set_plugins(plugin_dependencies_hash)

    def __files_to_backup(self):
        files_to_backup = []
        # Ask all plugins a list of files that I should backup for them
        for plugin in self.plugins:
            files_to_backup += plugin.files_to_backup()
        return files_to_backup

    def backup_files(self, files_to_backup = None):
        if files_to_backup == None:
            files_to_backup = self.__files_to_backup() + self.__conf_files()
        self.__my_print( "Backup folder : %s" % self.backup_dir )
        os.makedirs(self.backup_dir)
        os.makedirs(self.backup_dir + '/sjconf/')
        out, err, exit_value = self.__exec_command("cp '%s' '%s'" % (self.confs['local'].file_path, self.backup_dir + os.path.basename(self.confs['local'].file_path)))
        if exit_value != 0:
            raise err
        # Store all files into a service dedicated folder
        for file_to_backup in files_to_backup:
            if not os.path.isfile(file_to_backup.path):
                continue
            if not os.path.isdir(self.backup_dir + '/' + file_to_backup.plugin_name):
                os.makedirs(self.backup_dir + '/' + file_to_backup.plugin_name)
            file_to_backup.backup_path = self.backup_dir + '/' + file_to_backup.plugin_name + '/' + os.path.basename(file_to_backup.path)
            out, err, exit_value = self.__exec_command("mv '%s' '%s'" % (file_to_backup.path, file_to_backup.backup_path))
            if exit_value != 0:
                raise (err + '\nPlease restore files manually from %s' % (self.backup_dir), files_to_backup)
            file_to_backup.backed_up = True
        return files_to_backup

    def __archive_backup(self):
        # Once configuration is saved, we can archive backup into a tgz
        path = "%s/sjconf_backup_%s.tgz" % (os.path.dirname(self.backup_dir), os.path.basename(self.backup_dir))
        self.__my_print("Backup file : %s" % path)
        out, err, exit_value = self.__exec_command("tar zcvf %s %s" % (path, self.backup_dir))
        if exit_value != 0:
            raise err +  "\nCannot archive backup dir %s/ to %s, please do it manually" % (self.backup_dir, path)

    def __delete_backup_dir(self, dir=None):
        # Once backup has been archived, delete it
        if dir == None:
            dir = self.backup_dir
            self.__my_print("Deleting folder %s" % dir)

        for entry in os.listdir(dir):
            path = dir + '/' + entry
            if os.path.isdir(path):
                self.__delete_backup_dir(path)
            elif os.path.isfile(path):
                os.unlink(path)
        os.rmdir(dir)

    def restore_files(self, backed_up_files):
        # Something went wrong
        self.__my_print("Restoring files from %s" % self.backup_dir)

        # Unlink all conf files just created
        for backed_up_file in backed_up_files:
            if backed_up_file.written and os.path.isfile(backed_up_file.path):
                os.unlink(backup_up_file.file_path)

        # Restore backup files
        for backed_up_file in backed_up_files:
            if backed_up_file.backed_up:
                out, err, exit_value = self.__exec_command("mv '%s' '%s'" % (backed_up_file.backup_path, backed_up_file.path))
                if exit_value != 0:
                    raise err + "\nPlease restore files manually from %s" % self.backup_dir

    def __apply_confs(self, conf_files = None):
        # Open and write all configuration files
        if conf_files == None:
            conf_files = self.__conf_files()
        for conf_file in conf_files:
            self.__my_print("Writing configuration file %s (%s)" % (conf_file.path, conf_file.plugin_name))
            # checking if the dirname exists
            folder = os.path.dirname(conf_file.path)
            if not os.path.isdir(folder):
                os.makedirs(folder)
            open(conf_file.path, "w").write(conf_file.content)
            conf_file.written = True
        self.__my_print('')

    def __conf_files(self):
        conf_files = []
        for plugin in self.plugins:
            conf_files += plugin.conf_files()
        return conf_files

    def __file_path(self, file_type, file_path):
        file_path = file_path
        if not file_path.startswith(self.files_path[file_type]):
            file_path = self.files_path[file_type] + '/' + file_path
        for extension in self.files_extensions[file_type]:
            if os.path.exists(file_path):
                break
            file_path += extension
        if not os.path.exists(file_path):
            raise IOError(errno.ENOENT, "file %s not installed, path: %s" % (file_path, file_path))
        return file_path
