## Server mode
```
git clone git@github.com:ALLATRA-IT/cloudobs.git
cd cloudobs
pip3 install -r requirements.txt
```
Add `.env`
```
python3 common_service.py
```

## Client mode
**Server mode has the same prerequisites as for the client, but you need only two service up**
```
python3 gdrive_sync.py
python3 instance_service.py
```

## Docker mode for server
### Native
```
docker build -t $(basename $(pwd)) . --no-cache
docker run -p 3000:3000 $(basename $(pwd))
```

### Docker Compose for server and ui
A complex solution to have both, server and ui control on the one host.

```
# Use different directory to clone
git clone https://github.com/ALLATRA-IT/cloudobs-client.git && cd cloudobs-client
docker build -t $(basename $(pwd)) . --no-cache
```
```
# Get back to the repository directory
cd cloudobs
docker build -t $(basename $(pwd)) . --no-cache
```

* Check you have all required images using command `docker images`
```
# You should see something like that
REPOSITORY        TAG       IMAGE ID       CREATED          SIZE
cloudobs-client   latest    5ac92675e9b0   27 minutes ago   383MB
cloudobs          latest    310b6bcc7d97   46 minutes ago   394MB
```

* Then start compose
```
docker-compose up -d
```
* Open http://localhost:3003
