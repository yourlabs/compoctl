"""
Wrapper around docker-compose with fetch support and extra commands.

So, compoctl up will do the same as docker-compose up. That said, we added an
``apply`` command that chains a snapshot/pull/down/up/logs/ps. compoctl will
support URLs for -f arguments, hence::

    # example to start dev cluster
    compoctl apply -f https://raw.../docker-compose.yml -f https://raw../docker-compose/dev.yml

    # example to start an environment you maintain on the network
    echo $staging_override > docker-compose.override.yml
    echo $ssh_key > ~/.ssh/id_ed25519
    rsync docker-compose.* deploy@server:/home/staging
    ssh deploy@server bash -exc "web_image=your/image:$CI_COMMIT_REF compoctl -p /home/staging apply"

    # of course, bash is swappable with an inventoryless playbook that could
    # also setup system dependencies (your load-balancer, monitoring
    # compose-declared stacks)
    ansible-apply -f tasks/deploy.yml override=$staging_override deploy@staging.example.com

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
import yaml


@cli2.command(color=cli2.GREEN)
def apply():
    """
    Chain pull/down/up/logs/ps.

    compoctl -f ./foo.yml apply
    # will run:
    docker-compose -f ./foo.yml pull
    docker-compose -f ./foo.yml down
    docker-compose -f ./foo.yml up -d
    docker-compose -f ./foo.yml logs
    """
    cmds = [
        ['pull'],
        ['down'],
        ['up', '-d'],
        ['logs'],
        ['ps'],
    ]
    for cmd in cmds:
        p = compose(*cmd)
        if p.returncode != 0:
            raise cli2.Cli2Exception('Return code: ' + str(p.returncode))


def compose(command, *args):
    compose_argv = ['docker-compose'] + console_script.options
    compose_argv.append(command)
    compose_argv += list(args) + console_script.args
    print(cli2.YELLOW + 'Running ' + cli2.RESET + ' '.join(compose_argv))

    p = subprocess.Popen(
        compose_argv,
        stderr=sys.stderr,
        stdin=sys.stdin,
        stdout=sys.stdout,
    )
    p.communicate()
    return p


@cli2.command(color=cli2.YELLOW)
def backup():
    """
    Execute the container backup commands.

    Example configuration that makes it work as-is:

        volumes:
        - ./backup/postgres:/backup
        labels:
          compoctl.backup: pg_dumpall -U postgres -f /backup/data.dump
    """
    if not os.path.exists('./backup'):
        yield 'Creating ./backup'
        os.makedirs('./backup')

    '''
    for i in glob.glob('docker-compose*.yml'):
        yield f'Copying {i}'
        try:
            shutil.copyfile(i, f'./backup/{i}')
        except PermissionError:
            yield cli2.RED + 'Permission error writing ./backup' + cli2.RESET
            yield cli2.GREEN + 'HINT' + cli2.RESET + ' Try sudo -E compoctl'
            raise cli2.Cli2Exception('Permission error')
    '''

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
        image = cfg[0]['Image']
        images[service] = image

    cfg = subprocess.check_output(
        'docker-compose config', shell=True
    ).decode('utf8')
    cfg = yaml.load(cfg)

    for service, image in images.items():
        cfg['services'][service]['image'] = image
    restore_content = yaml.dump(cfg)
    restore_path = './backup/docker-compose.restore.yml'
    yield f'Writing {restore_path} with hard coded images'
    with open(restore_path, 'w+') as fh:
        fh.write(restore_content)

    ran = False
    for name, service in cfg.get('services', {}).items():
        backup_cmd = service.get('labels', {}).get('io.compoctl.backup.cmd', None)
        if not backup_cmd:
            continue
        p = compose('exec', name, *shlex.split(backup_cmd))
        if p.returncode != 0:
            raise cli2.Cli2Exception('Backup exited with non-0 !')
        ran = True

    if not ran:
        yield cli2.RED + 'No $backup_cmd found: no data backup !' + cli2.RESET


@cli2.command(color=cli2.RED)
def restore():
    """
    Copy docker-compose.yml back from ./backup and run restore commands.

    Example configuration that makes it work as-is:

        volumes:
        - ./backup/postgres:/backup
        labels:
          compoctl.restore: psql -U postgres -f /backup/data.dump
    """
    if not os.path.exists('./backup/docker-compose.restore.yml'):
        raise cli2.Cli2Exception('./backup not found !')

    shutil.copyfile(
        './backup/docker-compose.restore.yml',
        'docker-compose.restore.yml',
    )

    console_script.options += ['-f', 'docker-compose.restore.yml']

    with open('docker-compose.restore.yml', 'r') as fh:
        content = fh.read()
    cfg = yaml.load(content)

    ran = False
    for name, service in cfg.get('services', {}).items():
        cmd = service.get('labels', {}).get('io.compoctl.restore', None)
        if not cmd:
            continue
        p = compose('exec', name, *shlex.split(cmd))
        if p.returncode != 0:
            raise cli2.Cli2Exception('Backup exited with non-0 !')
        ran = True

    if not ran:
        yield cli2.RED + 'No restore cmd found: no data restore !' + cli2.RESET



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
                compose(name)
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

        def get(name):
            if name.startswith('http') and '://' in name:
                content = requests.get(name).content
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
                self.options += ['-f', get(self.parser.argv_all[num + 1])]
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

    def call(self, command):
        self.compose_argv_handle()
        return super().call(command)


console_script = ConsoleScript(__doc__)
