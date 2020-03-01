## HTTP Service Builder Integrated with Git
Builds every branch of an HTTP Service and maps it to the URL alias /$BRANCH/. It works by building GoCD, a GoCD agent, and nginx. It adds jobs to GoCD for every git branch, and it configures nginx to reverse proxy to the appropriate $PORT for requests to /$BRANCH/. The GoCD Agent is configured to build sibling docker containers, so you can run docker commands in the job script as you'd expect.


### Example

```
python3 build_server.py --job mormo_job.sh --git_url https://github.com/joeystevens00/mormo --verbose
```

#### Job Script
The job script should bind to $PORT and build $BRANCH.

```
docker build -t mormo:$BRANCH .
bash -c "docker container rm -f $BRANCH || echo -n"
docker run -d --rm -p $PORT:$PORT --env REDIS_HOST=172.17.0.3 --name $BRANCH mormo:$BRANCH uvicorn --port $PORT --host 0.0.0.0 mormo.api:app
```

The script will finish quickly, go to http://localhost:8153/go/pipelines#!/ to get a real idea of the progress.

Once all of the pipelines are finished running there should be containers like:
```
$ docker container ls
CONTAINER ID        IMAGE                      COMMAND                  CREATED             STATUS              PORTS                              NAMES
524d3d67b27a        mormo:0.7                  "uvicorn --port 9000…"   2 minutes ago       Up About a minute   0.0.0.0:9000->9000/tcp             0.7
b4504793126b        mormo:master               "uvicorn --port 9001…"   10 minutes ago      Up 10 minutes       0.0.0.0:9001->9001/tcp             master
c7de05617c52        nginx                      "nginx -g 'daemon of…"   21 minutes ago      Up 20 minutes       0.0.0.0:80->80/tcp                 api_buiilder_nginx
8d5e71220cf4        gocd-agent                 "/docker-entrypoint.…"   22 minutes ago      Up 22 minutes                                          mystifying_gould
64b26585e398        gocd/gocd-server:v20.1.0   "/docker-entrypoint.…"   22 minutes ago      Up 22 minutes       0.0.0.0:8153-8154->8153-8154/tcp   interesting_herschel
```
