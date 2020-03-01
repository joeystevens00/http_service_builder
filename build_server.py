import argparse
from collections import defaultdict
import json
import logging
import os
import re
import tempfile
import time
from typing import List
import pathlib

import git
from gomatic import *
import docker

logging.basicConfig()
logger = logging.getLogger(__name__)
curdir = pathlib.Path(__file__).parent.absolute()


CMDS = re.compile(rf'(\'.*?\'|\".*?\"|(?:(?!\s).)+)')


def get_branches_remote_repo(git_repo: str):
    tmp = tempfile.TemporaryDirectory()
    repo = git.Repo.init(tmp.name)
    origin = repo.create_remote('origin', git_repo)
    origin.fetch()
    origin.pull(origin.refs[0].remote_head)
    branches = ['/'.join(b.split('/')[1:]) for b in repo.git.branch('-r').split('\n')]
    return branches


def replace_variables(line: List, variables: dict):
    nl = []
    for i in line:
        inserted_value = False
        for k, v in variables.items():
            var_str = '$'+k
            if var_str in i:
                nl.append(i.replace(var_str, str(v)).replace('"', '').replace("'", ''))
                inserted_value = True
        if not inserted_value:
            nl.append(i)
    return nl


def gocd_job(script, gocd_host=None, pipeline_name=None, git_url=None, job=None, env=None, branch='master'):
    logger.info(f'New GoCD Pipeline {pipeline_name} set to build {branch} of {git_url}')
    while True:
        try:
            configurator = GoCdConfigurator(HostRestClient(gocd_host, ssl=gocd_host.startswith('https://')))
            break
        except Exception as e:
            logger.debug("Waiting on GoCD server to start...")
            time.sleep(1)
    pipeline = configurator\
    	.ensure_pipeline_group("defaultGroup")\
    	.ensure_replacement_of_pipeline(pipeline_name)\
    	.set_git_url(git_url)\
        .set_git_material(GitMaterial(git_url, branch=branch))\
        .ensure_environment_variables(env)
    stage = pipeline.ensure_stage("deploy")
    job = stage.ensure_job("build")

    logger.debug('Script')
    for line in script.split('\n'):
        line = CMDS.findall(line)
        if line and line[0]:
            line = replace_variables(line, env)
            logger.debug(line)
            job.add_task(ExecTask(line))

    return configurator.save_updated_config(save_config_locally=True, dry_run=False)


def build_gocd_server(client):
    logger.info("Building GoCD Server")
    container = client.containers.run(
        'gocd/gocd-server:v20.1.0',
        ports={'8153/tcp': ('127.0.0.1', 8153), '8154/tcp': ('127.0.0.1', 8154)},
        detach=True,
    )
    gocd_ip = None
    while not gocd_ip:
        logger.debug("Waiting on IP address for GoCD Server...")
        container = client.containers.get(container.id)
        gocd_ip = container.attrs['NetworkSettings']['IPAddress']
        time.sleep(1)
    return container, gocd_ip


def build_gocd_agent(client, gocd_ip):
    logger.info("Building GoCD Agent")
    client.images.build(
        path='.',
        dockerfile='go_agent.dockerfile',
        tag='gocd-agent',
    )
    return client.containers.run(
        'gocd-agent',
        detach=True,
        privileged=False,
        environment={
            'GO_SERVER_URL':f'https://{gocd_ip}:8154/go',
            #'AGENT_AUTO_REGISTER_KEY': '388b633a88de126531afa41eff9aa69e',
            #'AGENT_AUTO_REGISTER_HOSTNAME': 'agent0',
        },
        volumes={
            '/var/run/docker.sock': {'bind': '/var/run/docker.sock', 'mode': 'rw'},
            str(curdir.joinpath('godata')): {'bind': '/godata', 'mode': 'rw'}
        }
    )


def build_gocd(client):
    server, gocd_ip = build_gocd_server(client)
    agent = build_gocd_agent(client, gocd_ip)
    return server, agent


def to_hex(x):
    return "".join([hex(ord(c))[2:].zfill(2) for c in x])


def build_nginx(client, port_map):
    locations = []
    docker_host = [
        c for c in client.containers.list()\
            if 'gocd/gocd-server' in str(c.image)
    ][0].attrs['NetworkSettings']['Gateway']
    logger.info("Building nginx")
    for branch, port in port_map.items():
        logger.debug(f"Proxying /{branch}/ to {port}")
        locations.append(f"""
            location /{branch}/ {{
                proxy_set_header Host $host;
                proxy_set_header X-Real-IP $remote_addr;
                proxy_pass http://{docker_host}:{port}/;
            }}
        """)
    location_str = "\n".join(locations)
    nginx_conf = f"""
        server {{
        	listen 80 default_server;
        	listen [::]:80 default_server;
        	root /var/www/html;
        	index index.html index.htm index.nginx-debian.html;
        	server_name _;
            {location_str}
        }}
    """
    nginx_file = str(curdir.joinpath('nginx.conf'))
    with open(nginx_file, 'w') as f:
        f.write(nginx_conf)

    old_container = [
        c for c in client.containers.list()
        if c.name == 'api_buiilder_nginx'
    ]
    if old_container:
        old_container[0].stop()
        old_container[0].remove()
    return client.containers.run(
        'nginx:latest',
        detach=True,
        name='api_buiilder_nginx',
        mounts=[docker.types.Mount(
            target='/etc/nginx/conf.d/default.conf',
            source=nginx_file,
            read_only=True,
            type='bind',
        )],
        ports={'80/tcp': 80},
    )


def gocd_update_job(args):
    """Runs self with --update as a GoCD job."""
    # The nginx bind causes issues because docker looks on the host filesystem for nginx.conf
    # need to include a volume to share data between nginx/gocd agent
    cmd = f"python3 /api_builder/build_server.py --job /api_builder/{args.job} --gocd_host {args.gocd_host} --git_url {args.git_url} --state_file /api_builder/{state_file_name} --update"
    configurator = GoCdConfigurator(HostRestClient(args.gocd_host, ssl=args.gocd_host.startswith('https://')))
    pipeline = configurator\
        .ensure_pipeline_group("defaultGroup")\
        .ensure_replacement_of_pipeline(f'{args.pipeline_prefix}_update')\
        .ensure_material(PipelineMaterial(last_built, "deploy"))
    stage = pipeline.ensure_stage("deploy")
    job = stage.ensure_job("build")
    for line in cmd.split('\n'):
        line = CMDS.findall(line)
        if line and line[0]:
            print(line)
            job.add_task(ExecTask(line))
    configurator.save_updated_config(save_config_locally=True, dry_run=False)


def main():
    parser = argparse.ArgumentParser(description='HTTP Service Build Server')
    parser.add_argument('--job', required=True, help='Shell script that brings up HTTP Service. The variables $BRANCH and $PORT are made available to the script, and the script is expected to result in a HTTP service listening on $PORT.')
    parser.add_argument('--gocd_host', default="localhost:8153", help='GoCD host to connect to')
    parser.add_argument('--pipeline_prefix', default='http_service', help='Prefix of the new pipeline')
    parser.add_argument('--git_url', required=True, help='git url for job')
    parser.add_argument('--verbose', action='store_true')
    parser.add_argument('--skip_build_gocd', action='store_true')
    parser.add_argument('--skip_build_nginx', action='store_true')
    parser.add_argument('--update', action='store_true', help='Add/Modify GoCD pipelines, rebuild nginx if new branches')
    parser.add_argument('--state_file')

    args = parser.parse_args()

    if args.verbose:
        logger.setLevel('DEBUG')

    with open(args.job, 'r') as f:
        script = f.read()

    state_file_name = '.build_server_state.json'
    state_file = args.state_file or str(curdir.joinpath(state_file_name))

    if os.path.exists(state_file):
        with open(state_file, 'r') as f:
            initial_state = json.load(f)
    else:
        initial_state = defaultdict(lambda: {'port_map': {}}, {'next_port': 9000})

    client = docker.from_env()
    if not args.skip_build_gocd and not args.update:
        server, agent = build_gocd(client)
    gocd_job_args = {
        'job': args.job,
        'gocd_host': args.gocd_host,
        'git_url': args.git_url,
    }
    git_branches = get_branches_remote_repo(args.git_url)

    last_built = None
    for branch in git_branches:
        pipeline_name = f'{args.pipeline_prefix}_{branch}'
        incr_port = True
        if initial_state[pipeline_name]['port_map'].get(branch):
            port = initial_state[pipeline_name]['port_map'].get(branch)
            incr_port = False
        else:
            port = initial_state['next_port']
        env = {'PORT': str(port), 'BRANCH': branch}
        initial_state[pipeline_name]['port_map'][branch] = port
        gocd_job(script, **gocd_job_args, env=env, branch=branch, pipeline_name=pipeline_name)
        if incr_port:
            initial_state['next_port'] += 1
        last_built = pipeline_name

    if not args.skip_build_nginx:
        build_nginx(
            client,
            {
                branch: port
                for pipeline, port_map in initial_state.items()
                for branch, port in (port_map['port_map']
                if isinstance(port_map, dict) else {}).items()
            }
        )

    # Update job
    # gocd_update_job(args)

    logger.debug("State:" + repr(dict(initial_state)))
    logger.info(f"Manually enable the GoCD Agent at {args.gocd_host}/go/agents")
    with open(state_file, 'w') as f:
        json.dump(initial_state, f)


if __name__ == "__main__":
    main()
