"""
Wrapper around docker-compose with fetch support and extra commands.

So, compoctl up will do the same as docker-compose up. Added:

- apply command that chains a pull/build/down/up/logs/ps, supports -f http://..
- backup/restore commands that use ./backup directory.

Docker compose needs to mount ./backup and define backup and restore commands,
example config:

  postgres:
    volumes:
    - postgres-data:/var/lib/postgresql/data
    - ./backup/postgres:/backup
    labels:
      io.yourlabs.backup.cmd: pg_dumpall -U ${POSTGRES_USER-postgres} -f /backup/data.dump
      io.yourlabs.restore.cmd: |
        psql -U ${POSTGRES_USER-postgres} -f /backup/data.dump &> /backup/restore.log

Usage examples:

    # example to start dev cluster
    compoctl apply -f https://raw...compose.yml -f https://raw..compose.dev.yml

    # example to start an environment you maintain on the network
    echo $staging_override > docker-compose.override.yml
    echo $ssh_key > ~/.ssh/id_ed25519
    scp -r docker-compose.* deploy@server:/home/staging
    ssh deploy@server bash -exc "web_image=your/image:$CI_COMMIT_REF compoctl -p /home/staging apply"

"""

import cli2
import json
import glob
import os
import requests
import shlex
import shutil
import subprocess
import sys
import time
import yaml


@cli2.command(color=cli2.GREEN)
def apply():
    """
    Chain pull/build/down/up/logs/ps.

    compoctl -f ./foo.yml apply

    # will run:
    docker-compose -f ./foo.yml pull
    docker-compose -f ./foo.yml build
    docker-compose -f ./foo.yml down
    docker-compose -f ./foo.yml up -d
    docker-compose -f ./foo.yml logs
    docker-compose -f ./foo.yml ps
    """
    cmds = [
        ['pull'],
        ['build'],
        ['down'],
        ['up', '-d'],
        ['logs'],
        ['ps'],
    ]
    for cmd in cmds:
        p = compose(*cmd)
        if p.returncode != 0:
            raise cli2.Cli2Exception('Return code: ' + str(p.returncode))


def compose(command, *args, **kwargs):
    compose_argv = console_script.compose_cmd(command, *args)
    print(cli2.YELLOW + 'Running ' + cli2.RESET + ' '.join(compose_argv))

    kwargs.setdefault('stderr', sys.stderr)
    kwargs.setdefault('stdin', sys.stdin)
    kwargs.setdefault('stdout', sys.stdout)

    p = subprocess.Popen(
        compose_argv,
        **kwargs
    )
    p.communicate()
    return p


@cli2.command(color=cli2.YELLOW)
def backup():
    """
    Backup data into ./backup.

    Example configuration that makes it work as-is:

        volumes:
        - ./backup/postgres:/backup
        labels:
          io.yourlabs.backup.cmd: pg_dumpall -U postgres -f /backup/data.dump

    This will dump pg data into ./backup/postgres, and also export
    docker-compose running config into ./backup/docker-compose._restore.yml

    It will also execute the docker-compose.backup.yml if it exists. This is
    were you can spawn a container that mounts ./backup and proceeds to the
    secure backup export over the network that you want for production.

    To prevent permission issues, containers should at no time write the
    ./backup directory itself.
    """
    if not os.path.exists('./backup'):
        yield 'Creating ./backup'
        os.makedirs('./backup')

    images = dict()
    cids = subprocess.check_output(
        'docker-compose ps -q', shell=True
    ).decode('utf8').split('\n')
    for cid in cids:
        if not cid:
            continue

        cfg = subprocess.check_output(
            'docker inspect ' + cid, shell=True
        ).decode('utf8')
        cfg = json.loads(cfg)
        service = cfg[0]['Config']['Labels']['com.docker.compose.service']
        image = cfg[0]['Config']['Image']
        images[service] = image

    cfg = console_script.config()

    for service, image in images.items():
        cfg['services'][service]['image'] = image
    restore_content = yaml.dump(cfg)
    restore_path = './backup/docker-compose._restore.yml'
    yield f'Writing {restore_path} with hard coded images'
    with open(restore_path, 'w+') as fh:
        fh.write(restore_content)

    ran = False
    for name, service in cfg.get('services', {}).items():
        backup_cmd = service.get('labels', {}).get('io.yourlabs.backup.cmd', None)
        if not backup_cmd:
            continue
        p = compose('exec', name, 'sh', '-c', backup_cmd)
        if p.returncode != 0:
            raise cli2.Cli2Exception('Backup exited with non-0 !')
        ran = True

    if not ran:
        yield cli2.RED + 'No $backup_cmd found: no data backup !' + cli2.RESET


@cli2.command(color=cli2.RED)
def restore():
    """
    Copy docker-compose.yml back from ./backup and run restore commands.

    This is a destructive operation that will delete all volumes except the
    backup volume, up each service one by one and apply the restore command.

    Example configuration that makes it work as-is:

        volumes:
        - ./backup/postgres:/backup
        labels:
          compoctl.restore: psql -U postgres -f /backup/data.dump

    Note that the ./backup directory must have been provisioned with the backup
    command priorly.

    Also, the cluster will be unusable/down during the restore operation.
    """
    if not os.path.exists('./backup/docker-compose._restore.yml'):
        raise cli2.Cli2Exception('./backup not found !')

    shutil.copyfile(
        './backup/docker-compose._restore.yml',
        'docker-compose._restore.yml',
    )

    console_script.options += ['-f', './docker-compose._restore.yml']

    compose('pull')
    compose('down')

    with open('docker-compose._restore.yml', 'r') as fh:
        content = fh.read()
    cfg = yaml.safe_load(content)

    project = os.getcwd().split('/')[-1]
    ran = False
    for name, service in cfg.get('services', {}).items():
        cmd = service.get('labels', {}).get('io.yourlabs.restore.cmd', None)
        if not cmd:
            continue

        for volume in service.get('volumes', []):
            parts = volume.split(':')
            if 'backup' in parts[0]:
                continue
            elif '/' in parts[0]:
                print(cli2.RED + 'rm -f ' + cli2.RESET + parts[0])
                shutil.rmtree(parts[0])
            else:
                volume = '_'.join([project, name])
                print(cli2.RED + 'docker volume rm ' + cli2.RESET + volume)
                p = subprocess.Popen(
                    ['docker', 'volume', 'rm', volume],
                    stderr=sys.stderr,
                    stdin=sys.stdin,
                    stdout=sys.stdout,
                )
                p.communicate()

        p = compose('up', '-d', name)
        if p.returncode != 0:
            raise cli2.Cli2Exception(f'Start {name} non-0 !')

        print(cli2.YELLOW + 'Waiting service up: ' + cli2.RESET + name)
        time.sleep(5)
        p = compose('exec', name, *shlex.split(cmd))

        if p.returncode != 0:
            raise cli2.Cli2Exception(f'Restore {name} exited with non-0 !')

        ran = True

    if not ran:
        yield cli2.RED + 'No restore cmd found: no data restore !' + cli2.RESET

    compose('up', '-d')
    compose('logs')
    compose('ps')


class ConsoleScript(cli2.ConsoleScript):
    compose_commands = dict(
        build   = 'Build or rebuild services',
        bundle  = 'Generate a Docker bundle from the Compose file',
        config  = 'Validate and view the Compose file',
        create  = 'Create services',
        down    = 'Stop and remove containers, networks, images, and volumes',
        events  = 'Receive real time events from containers',
        exec    = 'Execute a command in a running container',
        images  = 'List images',
        kill    = 'Kill containers',
        logs    = 'View output from containers',
        pause   = 'Pause services',
        port    = 'Print the public port for a port binding',
        ps      = 'List containers',
        pull    = 'Pull service images',
        push    = 'Push service images',
        restart = 'Restart services',
        rm      = 'Remove stopped containers',
        run     = 'Run a one-off command',
        scale   = 'Set number of containers for a service',
        start   = 'Start services',
        stop    = 'Stop services',
        top     = 'Display the running processes',
        unpause = 'Unpause services',
        up      = 'Create and start containers',
        version = 'Show the Docker-Compose version information',
    )

    def __init__(self, *args, **kwargs):
        self.add_commands(apply, backup, restore)
        self.compose_commands_add()
        super().__init__(*args, **kwargs)

    def compose_commands_add(self):
        commands = []
        def _cmd(name, doc):
            def cmd():
                compose(name, *console_script.args)
            cmd.__doc__ = doc
            cmd.__name__ = name
            return cmd

        for name, doc in self.compose_commands.items():
            cmd = _cmd(name, doc)
            commands.append(cmd)
        self.add_commands(*commands)

    def compose_argv_handle(self):
        self.options = []
        self.command = 'help'
        self.args = []
        self.files = []

        def get(name):
            if name.startswith('http') and '://' in name:
                content = requests.get(name).content.decode('utf8')
                name = name.split('/')[-1]
                with open(name, 'w+') as fh:
                    fh.write(content)
            return name

        skip = False
        command_found = False
        for num, arg in enumerate(self.parser.argv_all):
            if skip:
                skip = False
                continue

            elif arg == '-f':
                f = self.parser.argv_all[num + 1]
                self.options += ['-f', get(f)]
                self.files.append(f)
                skip = True

            elif arg.startswith('--file'):
                if '=' in arg:
                    self.options += ['-f', get(arg.split('=')[1])]
                else:
                    self.options += ['-f', get(self.parser.argv_all[num + 1])]
                    skip = True

            elif arg in self.keys() and not command_found:
                self.command = arg
                command_found = True

            elif not command_found:
                self.options.append(arg)

            elif arg in self.parser.argv:
                self.args.append(arg)

        if not command_found:
            print(cli2.RED, 'Command not found')

        if not self.files:
            self.files.append('docker-compose.yml')
            if os.path.exists('docker-compose.override.yml'):
                self.files.append('docker-compose.override.yml')

    def config(self, service=None):
        return yaml.safe_load(
            subprocess.check_output(
                self.compose_cmd('config')))

    def call(self, command):
        self.compose_argv_handle()
        return super().call(command)

    def compose_cmd(self, *args):
        return ['docker-compose'] + self.options + list(args)


console_script = ConsoleScript(__doc__)
