# TITAN Development Tools

Utilities for development, testing, and analysis.

## 📁 Directory Structure

```
tools/
├── database/     # Database management tools
├── testing/      # Testing utilities
└── analysis/     # Data analysis tools
```

## 🗄️ Database Tools (`database/`)

### `init_database.py`
Initialize or reset the database schema.

```bash
python tools/database/init_database.py
```

**Functions:**
- Creates all tables
- Sets up TimescaleDB hypertables
- Creates indexes
- Seeds default budget

## 🧪 Testing Tools (`testing/`)

### `generate_test_signals.py`
Generate synthetic betting signals for testing.

```bash
python tools/testing/generate_test_signals.py
```

### `test_micro999_scraper.py`
Test the Micro999 scraper independently.

```bash
python tools/testing/test_micro999_scraper.py
```

### `test_ui_signals.py`
Test signal generation and UI display.

```bash
python tools/testing/test_ui_signals.py
```

## 📊 Analysis Tools (`analysis/`)

### `inspect_live_data.py`
Inspect live data in Redis.

```bash
python tools/analysis/inspect_live_data.py
```

**Shows:**
- Active matches
- Signal history
- Match states

### `show_live_data.py`
Display formatted live data.

```bash
python tools/analysis/show_live_data.py
```

### `show_sample_records.py`
Show sample records from database.

```bash
python tools/analysis/show_sample_records.py
```

### `capture_live_records.py`
Capture live data for analysis.

```bash
python tools/analysis/capture_live_records.py --output captures/
```

### `check_live_metadata.py`
Check metadata of live matches.

```bash
python tools/analysis/check_live_metadata.py
```

### `count_unique_matches.py`
Count unique matches in database.

```bash
python tools/analysis/count_unique_matches.py
```

## 🔧 Usage Examples

### Development Workflow

```bash
# 1. Initialize database
python tools/database/init_database.py

# 2. Test scraper
python tools/testing/test_micro999_scraper.py

# 3. Generate test signals
python tools/testing/generate_test_signals.py

# 4. Inspect results
python tools/analysis/inspect_live_data.py
```

### Debugging Live Issues

```bash
# Check what matches are live
python tools/analysis/show_live_data.py

# Inspect match metadata
python tools/analysis/check_live_metadata.py

# Capture data for later analysis
python tools/analysis/capture_live_records.py
```

### Database Management

```bash
# Reset database
python tools/database/init_database.py

# Count stored matches
python tools/analysis/count_unique_matches.py

# View sample records
python tools/analysis/show_sample_records.py
```

## 📝 Requirements

All tools require:
- Python 3.8+
- Dependencies from `requirements.txt`
- Running Docker services (for database/Redis access)

Install dependencies:
```bash
pip install -r requirements.txt
```

## 🔍 Tool Categories

**Database Tools** - Setup and management
**Testing Tools** - Verification and validation  
**Analysis Tools** - Data inspection and debugging

## 📚 Related Documentation

- [docs/user-guides/MANUAL_TESTING.md](../docs/user-guides/MANUAL_TESTING.md) - Testing guide
- [docs/deployment/OPERATIONS.md](../docs/deployment/OPERATIONS.md) - Operations guide

