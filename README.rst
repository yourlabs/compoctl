compoctl: backup/restore/apply for docker-compose
=================================================

compoctl backup
---------------

Backup data into ./backup.

Example configuration that makes it work as-is::

    volumes:
    - ./backup/postgres:/backup
    labels:
      io.compoctl.backup.cmd: pg_dumpall -U postgres -f /backup/data.dump

This will dump pg data into ./backup/postgres, and also export
docker-compose running config into ./backup/docker-compose.restore.yml

It will also execute the docker-compose.backup.yml if it exists. This is
were you can spawn a container that mounts ./backup and proceeds to the
secure backup export over the network that you want for production.

To prevent permission issues, containers should at no time write the
./backup directory itself.

compoctl restore
----------------

Copy docker-compose.yml back from ./backup and run restore commands.

This is a destructive operation that will delete all volumes except the
backup volume, up each service one by one and apply the restore command.

Example configuration that makes it work as-is::

    volumes:
    - ./backup/postgres:/backup
    labels:
      compoctl.restore: psql -U postgres -f /backup/data.dump

Note that the ./backup directory must have been provisioned with the backup
command priorly.

Also, the cluster will be unusable/down during the restore operation.

compoctl apply
--------------

Chain pull/down/up/logs/ps::

    compoctl -f ./foo.yml apply

    # will run:
    docker-compose -f ./foo.yml pull
    docker-compose -f ./foo.yml down
    docker-compose -f ./foo.yml up -d
    docker-compose -f ./foo.yml logs
    docker-compose -f ./foo.yml ps

Development status
------------------

POC working, will need tweaking to support more complex operations. The
objective is to stabilize the commands before proposing them upstream to
docker-compose.

Install
-------

Install with pip: pip install compoctl
