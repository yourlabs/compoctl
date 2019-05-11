compoctl: backup/restore/apply one-liner pipelines for docker-compose
=====================================================================

Install with pip install compoctl

A wrapper for the long docker-compose command that also adds a few features
that should be considered experimental, in waiting for refutation or upstream
contribution - which won't break BC because compoctl decorates docker-compose
commands.

compoctl apply: the one-liner pipeline for docker-compose
---------------------------------------------------------

Chain pull/build/down/up/logs/ps, nice to have for hacking and even more in
automated deploys::

    compoctl -f ./foo.yml apply

    # will run:
    docker-compose -f ./foo.yml pull
    docker-compose -f ./foo.yml build
    docker-compose -f ./foo.yml down
    docker-compose -f ./foo.yml up -d
    docker-compose -f ./foo.yml logs
    docker-compose -f ./foo.yml ps

compoctl backup
---------------

Backup data into ./backup.

Example configuration that makes it work as-is::

    volumes:
    - ./backup/postgres:/backup
    labels:
      io.yourlabs.backup.cmd: pg_dumpall -U postgres -f /backup/data.dump

This will dump pg data into ./backup/postgres, and also export
docker-compose running config into ./backup/docker-compose.restore.yml

It will also execute the docker-compose.backup.yml if it exists. This is
were you can spawn a container that mounts ./backup and proceeds to the
secure backup export over the network that you want for production.

To prevent permission issues, containers should at no time write the
./backup directory itself.

pre-POC state: waiting for an example chaining a docker-compose.backup.yml that
would spawn restic and rclone to backup the backup on a remote collection (or
implement retention policy feature into duplicity).

compoctl restore
----------------

Copy docker-compose.yml back from ./backup and run restore commands.

This is a destructive operation that will delete all volumes except the
backup volume, up each service one by one and apply the restore command.

Example configuration that makes it work as-is::

    volumes:
    - ./backup/postgres:/backup
    labels:
      io.yourlabs.restore.cmd: psql -U postgres -f /backup/data.dump

Note that the ./backup directory must have been provisioned with the backup
command priorly.

Also, the cluster will be unusable/down during the restore operation.
