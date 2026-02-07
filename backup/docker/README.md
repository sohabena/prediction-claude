# TITAN Docker Configuration

Docker configuration for the TITAN betting system infrastructure.

## 📦 Services

The `docker-compose.yml` file defines 4 core infrastructure services:

### 1. TimescaleDB (PostgreSQL)
- **Purpose:** Time-series database for match data, odds history, and bets
- **Port:** 5432
- **Image:** timescale/timescaledb:latest-pg14
- **Data:** Persists to `timescaledb_data` volume

### 2. Redis
- **Purpose:** Message broker and cache for real-time data
- **Port:** 6379  
- **Image:** redis:7-alpine
- **Data:** Persists to `redis_data` volume

### 3. PgAdmin
- **Purpose:** Database administration interface
- **Port:** 5050
- **Image:** dpage/pgadmin4:latest
- **Login:** admin@titan.com / titan_admin_2025

### 4. Redis Commander
- **Purpose:** Redis browser and management tool
- **Port:** 8081
- **Image:** redis commander/redis-commander:latest

## 🚀 Quick Start

### Start All Services
```bash
docker-compose up -d
```

### Stop All Services
```bash
docker-compose down
```

### View Logs
```bash
docker-compose logs -f
```

### Check Status
```bash
docker-compose ps
```

## 🔧 Configuration

### Environment Variables
Set these in `.env` file:

```env
# Database
POSTGRES_USER=titan_user
POSTGRES_PASSWORD=titan_db_2025
POSTGRES_DB=titan

# PgAdmin
PGADMIN_DEFAULT_EMAIL=admin@titan.com
PGADMIN_DEFAULT_PASSWORD=titan_admin_2025
```

### Ports
| Service | Port | Protocol |
|---------|------|----------|
| TimescaleDB | 5432 | PostgreSQL |
| Redis | 6379 | Redis |
| PgAdmin | 5050 | HTTP |
| Redis Commander | 8081 | HTTP |

## 🗄️ Data Persistence

Data is stored in Docker volumes:
- `timescaledb_data` - Database files
- `redis_data` - Redis snapshots  
- `pgadmin_data` - PgAdmin configuration

### Backup Volumes
```bash
# Backup TimescaleDB
docker exec titan_timescaledb pg_dump -U titan_user titan > backup.sql

# Backup Redis
docker exec titan_redis redis-cli SAVE
docker cp titan_redis:/data/dump.rdb backup.rdb
```

### Restore Volumes
```bash
# Restore TimescaleDB
cat backup.sql | docker exec -i titan_timescaledb psql -U titan_user -d titan

# Restore Redis
docker cp backup.rdb titan_redis:/data/dump.rdb
docker restart titan_redis
```

## 🔍 Accessing Services

### PgAdmin Web Interface
1. Open http://localhost:5050
2. Login with: admin@titan.com / titan_admin_2025
3. Add server:
   - Name: TITAN Local
   - Host: timescaledb (or localhost)
   - Port: 5432
   - Database: titan
   - Username: titan_user
   - Password: titan_db_2025

### Redis Commander Web Interface
1. Open http://localhost:8081
2. Browse Redis keys
3. View real-time data

### Database CLI
```bash
# Connect to PostgreSQL
docker exec -it titan_timescaledb psql -U titan_user -d titan

# Connect to Redis
docker exec -it titan_redis redis-cli
```

## 🧹 Maintenance

### Remove All Data (Fresh Start)
```bash
docker-compose down -v
docker-compose up -d
```

### View Resource Usage
```bash
docker stats
```

### Update Images
```bash
docker-compose pull
docker-compose up -d
```

## 🐛 Troubleshooting

### Containers Won't Start
```bash
# Check logs
docker-compose logs

# Check Docker daemon
docker ps

# Restart Docker Desktop
```

### Port Conflicts
```bash
# Check what's using ports
netstat -ano | findstr :5432
netstat -ano | findstr :6379
netstat -ano | findstr :5050
netstat -ano | findstr :8081
```

### Database Connection Issues
```bash
# Test connection
docker exec -it titan_timescaledb psql -U titan_user -d titan -c "SELECT 1;"

# Check TimescaleDB extension
docker exec -it titan_timescaledb psql -U titan_user -d titan -c "\dx"
```

### Redis Connection Issues
```bash
# Test Redis
docker exec -it titan_redis redis-cli PING

# Check Redis keys
docker exec -it titan_redis redis-cli KEYS "*"
```

## 📊 Health Checks

All services include health checks:
- **TimescaleDB:** `pg_isready`
- **Redis:** `redis-cli ping`
- **PgAdmin:** HTTP check
- **Redis Commander:** HTTP check

View health status:
```bash
docker-compose ps
```

## 🔐 Security Notes

**Development Mode:**
- Default credentials are set for convenience
- Services bind to localhost only
- Data persists in local volumes

**Production Mode:**
- Change all default passwords
- Use secrets management
- Enable SSL/TLS
- Configure firewall rules
- Use Docker secrets instead of environment variables

## 📚 Related Documentation

- [Main README](../README.md) - Project overview
- [Operations Guide](../docs/deployment/OPERATIONS.md) - Service management
- [Local Deployment](../docs/deployment/LOCAL_DEPLOYMENT.md) - Full setup guide

## 🔗 Useful Commands

```bash
# Start services
docker-compose up -d

# Stop services
docker-compose down

# Restart a single service
docker-compose restart timescaledb

# View logs for specific service
docker-compose logs -f redis

# Execute command in container
docker exec -it titan_timescaledb bash

# Remove everything (including volumes)
docker-compose down -v
```

