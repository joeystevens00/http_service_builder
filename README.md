## HTTP Service Builder Integrated with Git
Builds every branch of an HTTP Service and maps it to the URL alias /$BRANCH/. It works by building GoCD, a GoCD agent, and nginx. It adds jobs to GoCD for every git branch, and it configures nginx to reverse proxy to the appropriate $PORT for requests to /$BRANCH/. The GoCD Agent is configured to build sibling docker containers, so you can run docker commands in the job script as you'd expect.


### Example

```
python3 build_server.py --job mormo_job.sh --git_url https://github.com/joeystevens00/mormo
```

#### Job Script
The job script should bind to $PORT and build $BRANCH. 

```
docker build -t mormo:$BRANCH .
bash -c "docker container rm -f $BRANCH || echo -n"
docker run -d --rm -p $PORT:$PORT --env REDIS_HOST=172.17.0.3 --name $BRANCH mormo:$BRANCH uvicorn --port $PORT --host 0.0.0.0 mormo.api:app
```
