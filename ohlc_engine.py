"""
Real-time OHLC Data Collection Engine

PURPOSE:
Fetch live quotes for all assets in assets_select.csv and store in ohlc.db
Future integration: OI system alerts and entry/exit triggers

ARCHITECTURE:
- AssetLoader: Read assets_select.csv
- DhanQuoteFetcher: API interface for quotes
- OHLCDatabase: SQLite storage in ohlc.db
- OHLCEngine: Main orchestrator with configurable refresh rate

REFRESH RATE: 5 seconds (easily configurable via REFRESH_INTERVAL_SECONDS)
"""

import os
import sys
import time
import logging
import sqlite3
import pandas as pd
import requests
from datetime import datetime, timedelta
from contextlib import contextmanager
from typing import Dict, List, Optional, Tuple
from dotenv import load_dotenv
import signal
import threading
import zmq
import json

# ================================
# CONFIGURATION SECTION
# ================================

# Main refresh rate - change this value to adjust polling frequency
REFRESH_INTERVAL_SECONDS = 5  # TODO: Change this to adjust refresh rate (1-60 seconds recommended)

# Database configuration (follows same pattern as main dashboard)
DATABASE_NAME = "ohlc.db"
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DATABASE_PATH = os.path.join(DATA_DIR, DATABASE_NAME)

# Database settings (consistent with dashboard.py)
DATABASE_TIMEOUT = float(os.getenv('DB_TIMEOUT', '30.0'))
DATABASE_CACHE_SIZE = int(os.getenv('DB_CACHE_SIZE', '-64000'))

# API configuration
API_RATE_LIMIT_SECONDS = 1.0  # Dhan API limit: 1 request per second
API_TIMEOUT_SECONDS = 30

# Data retention (optional - for future cleanup)
DATA_RETENTION_DAYS = 7  # Keep 7 days of OHLC data

# Logging configuration
LOG_LEVEL = logging.INFO
LOG_DIR = os.path.join(DATA_DIR, "logs")
LOG_FILE = os.path.join(LOG_DIR, "ohlc_engine.log")

# ZMQ configuration for price broadcasting
ZMQ_PUBLISH_PORT = 5556  # Different from engine.py (uses 5555)
ZMQ_PUBLISH_ADDRESS = f"tcp://127.0.0.1:{ZMQ_PUBLISH_PORT}"

# ================================
# LOGGING SETUP
# ================================

# Ensure log directory exists
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=LOG_LEVEL,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_FILE)
    ]
)
logger = logging.getLogger(__name__)

# ================================
# ASSET LOADER
# ================================

class AssetLoader:
    """Load and manage assets from assets_select.csv"""

    def __init__(self, csv_path: str = "assets/assets_select.csv"):
        self.csv_path = csv_path
        self.assets_df = None
        self.grouped_assets = None

    def load_assets(self) -> bool:
        """Load assets from CSV file"""
        try:
            if not os.path.exists(self.csv_path):
                logger.error(f"Assets file not found: {self.csv_path}")
                return False

            self.assets_df = pd.read_csv(self.csv_path)

            # Validate required columns
            required_columns = ['asset_id', 'asset_name', 'exchange', 'segment_code']
            missing_columns = [col for col in required_columns if col not in self.assets_df.columns]

            if missing_columns:
                logger.error(f"Missing required columns in CSV: {missing_columns}")
                return False

            # Clean data - bulletproof approach for mixed data types
            try:
                # Clean column names safely
                self.assets_df.columns = self.assets_df.columns.str.strip()
            except Exception as e:
                logger.warning(f"Could not clean column names: {e}")

            # Clean string data safely - only apply str operations to string columns
            for col in self.assets_df.columns:
                try:
                    if self.assets_df[col].dtype == "object":
                        # Check if column actually contains string data before using .str accessor
                        non_null_data = self.assets_df[col].dropna()
                        if len(non_null_data) > 0 and isinstance(non_null_data.iloc[0], str):
                            self.assets_df[col] = self.assets_df[col].astype(str).str.strip()
                        else:
                            logger.debug(f"Skipping string operations on column '{col}' - contains non-string data")
                except Exception as e:
                    logger.warning(f"Could not clean column '{col}': {e}")

            # Group assets by exchange segment for API efficiency
            self._group_assets_by_exchange()

            logger.info(f"Loaded {len(self.assets_df)} assets from {self.csv_path}")
            logger.info(f"Exchange segments: {list(self.grouped_assets.keys())}")

            return True

        except Exception as e:
            logger.error(f"Failed to load assets: {e}")
            return False

    def _group_assets_by_exchange(self):
        """Group assets by exchange segment for API request format"""
        self.grouped_assets = {}

        for _, row in self.assets_df.iterrows():
            segment_code = row['segment_code']  # e.g., "NSE_EQ", "IDX_I"
            asset_id = int(row['asset_id'])

            if segment_code not in self.grouped_assets:
                self.grouped_assets[segment_code] = []

            self.grouped_assets[segment_code].append(asset_id)

        logger.debug(f"Grouped assets by segment: {self.grouped_assets}")

    def get_grouped_assets(self) -> Dict[str, List[int]]:
        """Get assets grouped by exchange segment (for API request)"""
        return self.grouped_assets or {}

    def get_asset_info(self, asset_id: int) -> Optional[Dict]:
        """Get asset information by asset_id"""
        if self.assets_df is None:
            return None

        asset_row = self.assets_df[self.assets_df['asset_id'] == asset_id]

        if asset_row.empty:
            return None

        return asset_row.iloc[0].to_dict()

# ================================
# DHAN QUOTE FETCHER
# ================================

class DhanQuoteFetcher:
    """Fetch quotes from Dhan API (based on live.py implementation)"""

    def __init__(self):
        self.access_token = None
        self.client_id = None
        self.last_api_call = 0
        self._load_credentials()

    def _load_credentials(self):
        """Load API credentials from environment"""
        load_dotenv(override=True)

        self.access_token = os.getenv('DHAN_ACCESS_TOKEN')
        self.client_id = os.getenv('DHAN_CLIENT_ID')

        if not self.access_token or not self.client_id:
            logger.error("Missing DHAN_ACCESS_TOKEN or DHAN_CLIENT_ID in .env file")
            raise ValueError("API credentials not found")

        logger.info("API credentials loaded successfully")

    def _respect_rate_limit(self):
        """Ensure we don't exceed API rate limit"""
        time_since_last = time.time() - self.last_api_call
        if time_since_last < API_RATE_LIMIT_SECONDS:
            sleep_time = API_RATE_LIMIT_SECONDS - time_since_last
            logger.debug(f"Rate limiting: sleeping for {sleep_time:.2f} seconds")
            time.sleep(sleep_time)

    def fetch_quotes(self, grouped_assets: Dict[str, List[int]]) -> Optional[Dict]:
        """
        Fetch OHLC quotes for grouped assets

        Args:
            grouped_assets: {"NSE_EQ": [asset_id1, asset_id2], "NSE_IDX": [asset_id3]}

        Returns:
            API response data or None if failed
        """
        try:
            self._respect_rate_limit()

            headers = {
                'Accept': 'application/json',
                'Content-Type': 'application/json',
                'access-token': self.access_token,
                'client-id': self.client_id
            }

            url = "https://api.dhan.co/v2/marketfeed/quote"

            logger.debug(f"Making OHLC API request for {sum(len(assets) for assets in grouped_assets.values())} assets")

            response = requests.post(
                url,
                json=grouped_assets,
                headers=headers,
                timeout=API_TIMEOUT_SECONDS
            )

            self.last_api_call = time.time()

            if response.status_code != 200:
                logger.error(f"API request failed with status {response.status_code}: {response.text}")
                return None

            api_data = response.json()

            if api_data.get('status') != 'success':
                logger.error(f"API returned unsuccessful status: {api_data}")
                return None

            logger.info(f"Successfully fetched quotes for {sum(len(assets) for assets in grouped_assets.values())} assets")
            return api_data.get('data', {})

        except requests.exceptions.RequestException as e:
            logger.error(f"Network error during API request: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error during quote fetch: {e}")
            return None

# ================================
# OHLC DATABASE
# ================================

class OHLCDatabase:
    """SQLite database manager for OHLC data storage"""

    def __init__(self, db_path: str = DATABASE_PATH):
        self.db_path = db_path
        self._initialize_database()

    @contextmanager
    def get_connection(self):
        """Database connection context manager (same pattern as dashboard.py)"""
        conn = None
        try:
            # Ensure data directory exists
            os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

            # Connect with same settings as main dashboard
            conn = sqlite3.connect(self.db_path, timeout=DATABASE_TIMEOUT)
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.execute(f"PRAGMA cache_size = {DATABASE_CACHE_SIZE}")
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA temp_store = MEMORY")
            yield conn
        except sqlite3.Error as e:
            logger.error(f"Database error: {e}")
            if conn:
                conn.rollback()
            raise
        finally:
            if conn:
                conn.close()


    def _initialize_database(self):
        """Create database tables if they don't exist"""
        try:
            with self.get_connection() as conn:
                # Create OHLC data table with extended quote data
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS ohlc_data (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        asset_id INTEGER NOT NULL,
                        symbol TEXT NOT NULL,
                        segment_code TEXT NOT NULL,
                        last_price REAL,
                        open_price REAL,
                        high_price REAL,
                        low_price REAL,
                        close_price REAL,
                        change_amount REAL,
                        change_percent REAL,
                        volume INTEGER DEFAULT 0,  -- Cumulative daily volume (running total from API)
                        interval_volume INTEGER DEFAULT 0,  -- Volume traded in this 5-second interval
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        -- New quote endpoint data fields
                        average_price REAL,
                        last_quantity INTEGER,
                        last_trade_time TEXT,
                        net_change REAL,
                        buy_quantity INTEGER,
                        sell_quantity INTEGER,
                        upper_circuit_limit REAL,
                        lower_circuit_limit REAL,
                        open_interest INTEGER DEFAULT 0,
                        oi_day_high INTEGER DEFAULT 0,
                        oi_day_low INTEGER DEFAULT 0
                    )
                """)

                # Create indexes for better performance
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_ohlc_timestamp_asset
                    ON ohlc_data(timestamp, asset_id)
                """)

                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_ohlc_asset_timestamp
                    ON ohlc_data(asset_id, timestamp DESC)
                """)

                # Create market depth table for orderbook data (stores up to 5 levels but typically only 1-2 are active)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS market_depth (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        asset_id INTEGER NOT NULL,
                        segment_code TEXT NOT NULL,
                        side TEXT NOT NULL CHECK(side IN ('buy', 'sell')),
                        level INTEGER NOT NULL CHECK(level BETWEEN 1 AND 5),
                        price REAL,
                        quantity INTEGER,
                        orders INTEGER,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                """)

                # Create index for market depth queries
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_depth_timestamp_asset
                    ON market_depth(timestamp, asset_id, side)
                """)

                # Create metadata table for tracking
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS fetch_metadata (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        assets_count INTEGER NOT NULL,
                        fetch_duration_ms INTEGER,
                        api_status TEXT,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                """)

                # Create time block volumes table for volume trajectory analysis
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS time_block_volumes (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        date TEXT NOT NULL,
                        asset_id INTEGER NOT NULL,
                        symbol TEXT NOT NULL,
                        time_block INTEGER NOT NULL CHECK(time_block BETWEEN 1 AND 6),
                        cumulative_volume INTEGER NOT NULL DEFAULT 0,
                        timestamp TEXT NOT NULL,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(date, asset_id, time_block)
                    )
                """)

                # Create indexes for time block volumes queries
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_time_block_asset_date
                    ON time_block_volumes(asset_id, date DESC, time_block)
                """)

                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_time_block_date_block
                    ON time_block_volumes(date DESC, time_block)
                """)

                # Create time block averages table for rolling averages storage
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS time_block_averages (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        asset_id INTEGER NOT NULL,
                        time_block INTEGER NOT NULL CHECK(time_block BETWEEN 1 AND 6),
                        timeframe TEXT NOT NULL CHECK(timeframe IN ('22d', '100d')),
                        average_volume REAL NOT NULL DEFAULT 0,
                        last_updated TEXT NOT NULL,
                        data_points INTEGER NOT NULL DEFAULT 0,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(asset_id, time_block, timeframe)
                    )
                """)

                # Create index for time block averages queries
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_time_block_averages_asset
                    ON time_block_averages(asset_id, time_block, timeframe)
                """)

                conn.commit()
                logger.info(f"Database initialized at {self.db_path}")

        except Exception as e:
            logger.error(f"Failed to initialize database: {e}")
            raise

    def store_ohlc_data(self, ohlc_records: List[Dict]) -> bool:
        """Store OHLC data records in database"""
        try:
            if not ohlc_records:
                logger.warning("No OHLC records to store")
                return True

            with self.get_connection() as conn:
                conn.executemany("""
                    INSERT INTO ohlc_data (
                        timestamp, asset_id, symbol, segment_code,
                        last_price, open_price, high_price, low_price, close_price,
                        change_amount, change_percent, volume, interval_volume,
                        average_price, last_quantity, last_trade_time, net_change,
                        buy_quantity, sell_quantity, upper_circuit_limit, lower_circuit_limit,
                        open_interest, oi_day_high, oi_day_low
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, [
                    (
                        record['timestamp'],
                        record['asset_id'],
                        record['symbol'],
                        record['segment_code'],
                        record['last_price'],
                        record['open_price'],
                        record['high_price'],
                        record['low_price'],
                        record['close_price'],
                        record['change_amount'],
                        record['change_percent'],
                        record.get('volume', 0),
                        record.get('interval_volume', 0),
                        # New quote endpoint fields
                        record.get('average_price'),
                        record.get('last_quantity'),
                        record.get('last_trade_time'),
                        record.get('net_change'),
                        record.get('buy_quantity'),
                        record.get('sell_quantity'),
                        record.get('upper_circuit_limit'),
                        record.get('lower_circuit_limit'),
                        record.get('open_interest', 0),
                        record.get('oi_day_high', 0),
                        record.get('oi_day_low', 0)
                    ) for record in ohlc_records
                ])

                conn.commit()
                logger.info(f"Stored {len(ohlc_records)} OHLC records in database")
                return True

        except Exception as e:
            logger.error(f"Failed to store OHLC data: {e}")
            return False

    def log_fetch_metadata(self, assets_count: int, duration_ms: int, api_status: str):
        """Log fetch operation metadata"""
        try:
            with self.get_connection() as conn:
                conn.execute("""
                    INSERT INTO fetch_metadata (timestamp, assets_count, fetch_duration_ms, api_status)
                    VALUES (?, ?, ?, ?)
                """, (datetime.now().isoformat(), assets_count, duration_ms, api_status))

                conn.commit()

        except Exception as e:
            logger.error(f"Failed to log fetch metadata: {e}")

    def store_market_depth(self, depth_records: List[Dict]) -> bool:
        """Store market depth data records in database"""
        try:
            if not depth_records:
                logger.debug("No market depth records to store")
                return True

            with self.get_connection() as conn:
                conn.executemany("""
                    INSERT INTO market_depth (
                        timestamp, asset_id, segment_code, side, level,
                        price, quantity, orders
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, [
                    (
                        record['timestamp'],
                        record['asset_id'],
                        record['segment_code'],
                        record['side'],
                        record['level'],
                        record['price'],
                        record['quantity'],
                        record['orders']
                    ) for record in depth_records
                ])

                conn.commit()
                logger.debug(f"Stored {len(depth_records)} market depth records in database")
                return True

        except Exception as e:
            logger.error(f"Failed to store market depth data: {e}")
            return False

    def get_latest_prices(self, asset_ids: List[int] = None) -> pd.DataFrame:
        """Get latest prices for specified assets (for future alerts integration)"""
        try:
            with self.get_connection() as conn:
                if asset_ids:
                    placeholders = ','.join('?' * len(asset_ids))
                    query = f"""
                        SELECT asset_id, symbol, last_price, timestamp
                        FROM ohlc_data
                        WHERE asset_id IN ({placeholders})
                        AND timestamp = (
                            SELECT MAX(timestamp) FROM ohlc_data o2
                            WHERE o2.asset_id = ohlc_data.asset_id
                        )
                        ORDER BY symbol
                    """
                    return pd.read_sql_query(query, conn, params=asset_ids)
                else:
                    query = """
                        SELECT asset_id, symbol, last_price, timestamp
                        FROM ohlc_data
                        WHERE timestamp = (
                            SELECT MAX(timestamp) FROM ohlc_data o2
                            WHERE o2.asset_id = ohlc_data.asset_id
                        )
                        ORDER BY symbol
                    """
                    return pd.read_sql_query(query, conn)

        except Exception as e:
            logger.error(f"Failed to get latest prices: {e}")
            return pd.DataFrame()

# ================================
# MAIN OHLC ENGINE
# ================================

class OHLCEngine:
    """Main orchestrator for OHLC data collection"""

    def __init__(self, csv_path: str = "assets/assets_select.csv", refresh_interval: int = 5):
        self.asset_loader = AssetLoader(csv_path)
        self.quote_fetcher = DhanQuoteFetcher()
        self.database = OHLCDatabase()
        self.running = False
        self.fetch_thread = None
        self.fetch_count = 0
        self.success_count = 0
        self.error_count = 0
        self.last_fetch_time = None
        self.refresh_interval = refresh_interval  # Make refresh rate dynamic
        self.zmq_lock = threading.Lock()  # Thread safety for ZMQ operations

        # Initialize ZMQ publisher
        self.zmq_context = zmq.Context()
        self.zmq_publisher = self.zmq_context.socket(zmq.PUB)
        self.zmq_publisher.bind(ZMQ_PUBLISH_ADDRESS)
        logger.info(f"ZMQ Publisher initialized on {ZMQ_PUBLISH_ADDRESS}")

        # Auto-load assets during initialization (like OI engine does)
        self.asset_loader.load_assets()

    def start(self):
        """Start the OHLC data collection engine"""
        logger.info("=== OHLC Engine Starting ===")

        # Check if assets are loaded (they should be loaded during initialization)
        if self.asset_loader.assets_df is None or len(self.asset_loader.assets_df) == 0:
            logger.error("No assets loaded - cannot start engine")
            return False

        # Start fetching in background thread
        self.running = True
        self.fetch_thread = threading.Thread(target=self._fetch_loop, daemon=True)
        self.fetch_thread.start()

        logger.info(f"OHLC Engine started - fetching every {self.refresh_interval} seconds")
        return True

    def _publish_price_updates(self, records: List[Dict]) -> None:
        """
        Publish price updates via ZMQ for calculators (PriceDataManager)

        Args:
            records: List of OHLC records from database store
        """
        # Early exit if stopping
        if not self.running:
            return

        try:
            with self.zmq_lock:
                # Double-check after acquiring lock
                if not self.running:
                    return

                timestamp = datetime.now().isoformat()

                for record in records:
                    asset_id = record.get('asset_id')
                    segment_code = record.get('segment_code')
                    last_price = record.get('last_price')

                    # Skip if missing critical data
                    if not asset_id or not segment_code or last_price is None:
                        continue

                    # Create complete message with all fields required by PriceDataManager
                    # Must include: asset_id, symbol, segment_code, last_price, open_price,
                    # high_price, low_price, volume, average_price, interval_volume
                    message = {
                        'asset_id': asset_id,
                        'symbol': record.get('symbol'),
                        'segment_code': segment_code,
                        'last_price': last_price,
                        'open_price': record.get('open_price'),
                        'high_price': record.get('high_price'),
                        'low_price': record.get('low_price'),
                        'volume': record.get('volume'),
                        'average_price': record.get('average_price'),
                        'interval_volume': record.get('interval_volume'),
                        'timestamp': timestamp
                    }

                    # Publish
                    topic = f"PRICE.{asset_id}"
                    self.zmq_publisher.send_string(f"{topic} {json.dumps(message)}")

            logger.debug(f"Published {len(records)} price updates via ZMQ")

        except zmq.ZMQError as e:
            if self.running:  # Only log if not shutting down
                logger.error(f"ZMQ error publishing price updates: {e}")
        except Exception as e:
            logger.error(f"Error publishing price updates: {e}")

    def stop(self):
        """Stop the OHLC data collection engine"""
        logger.info("=== OHLC Engine Stopping ===")
        self.running = False

        if self.fetch_thread and self.fetch_thread.is_alive():
            self.fetch_thread.join(timeout=10)

        logger.info("OHLC Engine stopped (ZMQ remains active)")

    def cleanup(self):
        """Cleanup ZMQ resources - call only on final application shutdown"""
        with self.zmq_lock:
            try:
                self.zmq_publisher.close()
                self.zmq_context.term()
                logger.info("ZMQ publisher closed")
            except Exception as e:
                logger.error(f"Error closing ZMQ: {e}")

    def _calculate_interval_volume(self, asset_id: int, current_volume: int) -> int:
        """
        Calculate interval volume for VWAP deviation analysis.

        VOLUME SEMANTICS:
        - current_volume (param) = cumulative daily volume from API (running total)
        - previous_volume (from DB) = previous cumulative volume from last 5-sec snapshot
        - interval_volume (return) = difference = volume traded in this 5-second interval
        - Both 'volume' and 'interval_volume' are stored in ohlc_data table

        Args:
            asset_id: Asset ID
            current_volume: Current cumulative volume

        Returns:
            Volume for this 5-second interval
        """
        try:
            # Get previous cumulative volume from database
            with self.database.get_connection() as conn:
                cursor = conn.execute("""
                    SELECT volume FROM ohlc_data
                    WHERE asset_id = ?
                    ORDER BY timestamp DESC
                    LIMIT 1
                """, (asset_id,))

                result = cursor.fetchone()

                if result is None:
                    # First data point of the day - edge case handling
                    # Import locally to avoid namespace issues (following existing pattern)
                    from datetime import time as dt_time

                    current_time = datetime.now().time()
                    market_opening = dt_time(9, 15)

                    if current_time <= dt_time(9, 20):  # Within 5 minutes of market open
                        # Treat as opening volume
                        return current_volume
                    else:
                        # Safe default for mid-day start
                        return 0
                else:
                    previous_volume = result[0]
                    interval_volume = current_volume - previous_volume

                    # Sanity check - interval volume should be >= 0
                    return max(0, interval_volume)

        except Exception as e:
            logger.warning(f"Error calculating interval volume for asset {asset_id}: {e}")
            return 0

    def _fetch_loop(self):
        """Main fetch loop running in background thread"""
        while self.running:
            try:
                start_time = time.time()

                # Get grouped assets for API request
                grouped_assets = self.asset_loader.get_grouped_assets()

                if not grouped_assets:
                    logger.warning("No assets to fetch")
                    time.sleep(self.refresh_interval)
                    continue

                # Fetch quotes from API
                self.fetch_count += 1
                self.last_fetch_time = datetime.now()

                # Immediate verification to catch corruption
                if not isinstance(self.last_fetch_time, datetime):
                    logger.error(f"CRITICAL: last_fetch_time immediately corrupted to {type(self.last_fetch_time)}")
                    self.last_fetch_time = datetime.now()

                api_data = self.quote_fetcher.fetch_quotes(grouped_assets)

                if api_data:
                    # Process and store the data
                    ohlc_records, depth_records = self._process_api_data(api_data)

                    if ohlc_records:
                        success = self.database.store_ohlc_data(ohlc_records)

                        # Store market depth data if available
                        if depth_records and success:
                            depth_success = self.database.store_market_depth(depth_records)
                            success = success and depth_success

                        # Log fetch metadata
                        duration_ms = int((time.time() - start_time) * 1000)
                        assets_count = sum(len(assets) for assets in grouped_assets.values())
                        status = "success" if success else "storage_failed"

                        self.database.log_fetch_metadata(assets_count, duration_ms, status)

                        if success:
                            self.success_count += 1
                            logger.info(f"Fetch cycle completed: {len(ohlc_records)} records in {duration_ms}ms")

                            # Publish price updates via ZMQ
                            self._publish_price_updates(ohlc_records)

                            # Check for time block boundary and process volume analysis
                            # Bulletproof time handling - ensure we always have a datetime object
                            if isinstance(self.last_fetch_time, datetime):
                                current_time = self.last_fetch_time
                            else:
                                # Handle corrupted timestamp - log warning and use current time
                                if self.last_fetch_time is not None:
                                    logger.warning(f"last_fetch_time corrupted to {type(self.last_fetch_time)}: {self.last_fetch_time}")
                                current_time = datetime.now()

                            time_block = self._is_time_block_boundary(current_time)
                            if time_block:
                                self._process_time_block_analysis(current_time, time_block)
                        else:
                            self.error_count += 1
                            logger.error("Failed to store OHLC data")
                    else:
                        self.error_count += 1
                        logger.warning("No OHLC records generated from API data")
                else:
                    self.error_count += 1
                    logger.error("Failed to fetch quotes from API")

                # Wait for next cycle
                elapsed = time.time() - start_time
                sleep_time = max(0, self.refresh_interval - elapsed)

                if sleep_time > 0:
                    time.sleep(sleep_time)

            except Exception as e:
                logger.error(f"Error in fetch loop: {e}")
                time.sleep(self.refresh_interval)

    def _process_api_data(self, api_data: Dict) -> Tuple[List[Dict], List[Dict]]:
        """Process API response into OHLC records and market depth records"""
        ohlc_records = []
        depth_records = []
        timestamp = datetime.now().isoformat()

        try:
            for segment_code, securities in api_data.items():
                for asset_id_str, market_data in securities.items():
                    asset_id = int(asset_id_str)

                    # Get asset info
                    asset_info = self.asset_loader.get_asset_info(asset_id)
                    if not asset_info:
                        logger.warning(f"Asset info not found for ID {asset_id}")
                        continue

                    # Extract OHLC data
                    last_price = market_data.get('last_price', 0)
                    ohlc = market_data.get('ohlc', {})

                    open_price = ohlc.get('open', 0)
                    high_price = ohlc.get('high', 0)
                    low_price = ohlc.get('low', 0)
                    close_price = ohlc.get('close', 0)

                    # Calculate change
                    change_amount = 0
                    change_percent = 0
                    if close_price and close_price > 0:
                        change_amount = last_price - close_price
                        change_percent = (change_amount / close_price) * 100

                    # Calculate interval volume for VWAP deviation analysis
                    current_volume = market_data.get('volume', 0)
                    interval_volume = self._calculate_interval_volume(asset_id, current_volume)

                    # Create OHLC record with all quote endpoint data
                    ohlc_record = {
                        'timestamp': timestamp,
                        'asset_id': asset_id,
                        'symbol': asset_info['asset_name'],
                        'segment_code': segment_code,
                        'last_price': round(last_price, 2) if last_price else 0,
                        'open_price': round(open_price, 2) if open_price else 0,
                        'high_price': round(high_price, 2) if high_price else 0,
                        'low_price': round(low_price, 2) if low_price else 0,
                        'close_price': round(close_price, 2) if close_price else 0,
                        'change_amount': round(change_amount, 2),
                        'change_percent': round(change_percent, 2),
                        'volume': market_data.get('volume', 0),
                        'interval_volume': interval_volume,
                        # New quote endpoint fields
                        'average_price': round(market_data.get('average_price', 0), 2) if market_data.get('average_price') else None,
                        'last_quantity': market_data.get('last_quantity'),
                        'last_trade_time': market_data.get('last_trade_time'),
                        'net_change': round(market_data.get('net_change', 0), 2),
                        'buy_quantity': market_data.get('buy_quantity', 0),
                        'sell_quantity': market_data.get('sell_quantity', 0),
                        'upper_circuit_limit': round(market_data.get('upper_circuit_limit', 0), 2) if market_data.get('upper_circuit_limit') else None,
                        'lower_circuit_limit': round(market_data.get('lower_circuit_limit', 0), 2) if market_data.get('lower_circuit_limit') else None,
                        'open_interest': market_data.get('oi', 0),
                        'oi_day_high': market_data.get('oi_day_high', 0),
                        'oi_day_low': market_data.get('oi_day_low', 0)
                    }

                    ohlc_records.append(ohlc_record)

                    # Extract market depth data
                    depth_data = market_data.get('depth', {})
                    if depth_data:
                        # Process buy side depth (up to 5 levels)
                        buy_depth = depth_data.get('buy', [])
                        for level, level_data in enumerate(buy_depth, 1):
                            price = level_data.get('price', 0)
                            quantity = level_data.get('quantity', 0)
                            orders = level_data.get('orders', 0)

                            # Only store if there's actual data (price > 0)
                            if price > 0:
                                depth_records.append({
                                    'timestamp': timestamp,
                                    'asset_id': asset_id,
                                    'segment_code': segment_code,
                                    'side': 'buy',
                                    'level': level,
                                    'price': round(price, 2),
                                    'quantity': quantity,
                                    'orders': orders
                                })

                        # Process sell side depth (up to 5 levels)
                        sell_depth = depth_data.get('sell', [])
                        for level, level_data in enumerate(sell_depth, 1):
                            price = level_data.get('price', 0)
                            quantity = level_data.get('quantity', 0)
                            orders = level_data.get('orders', 0)

                            # Only store if there's actual data (price > 0)
                            if price > 0:
                                depth_records.append({
                                    'timestamp': timestamp,
                                    'asset_id': asset_id,
                                    'segment_code': segment_code,
                                    'side': 'sell',
                                    'level': level,
                                    'price': round(price, 2),
                                    'quantity': quantity,
                                    'orders': orders
                                })

            logger.debug(f"Processed {len(ohlc_records)} OHLC records and {len(depth_records)} market depth records from API data")

        except Exception as e:
            logger.error(f"Error processing API data: {e}")

        return ohlc_records, depth_records

    # ================================
    # GUI ACCESS METHODS
    # ================================

    def get_loaded_assets(self) -> List[Dict]:
        """Get list of loaded assets for GUI display"""
        if self.asset_loader.assets_df is None:
            return []

        assets_list = []
        for _, row in self.asset_loader.assets_df.iterrows():
            assets_list.append({
                'asset_id': row['asset_id'],
                'asset_name': row['asset_name'],
                'exchange': row['exchange'],
                'segment_code': row['segment_code']
            })

        return assets_list

    def get_engine_status(self) -> Dict:
        """Get current engine status and statistics for GUI display"""
        success_rate = 0
        if self.fetch_count > 0:
            success_rate = (self.success_count / self.fetch_count) * 100

        return {
            'running': self.running,
            'fetch_count': self.fetch_count,
            'success_count': self.success_count,
            'error_count': self.error_count,
            'success_rate': round(success_rate, 1),
            'last_fetch_time': self.last_fetch_time.isoformat() if self.last_fetch_time else None,
            'refresh_interval': self.refresh_interval,
            'loaded_assets_count': len(self.asset_loader.assets_df) if self.asset_loader.assets_df is not None else 0
        }

    def load_assets_from_csv(self, csv_path: str) -> bool:
        """Load assets from a different CSV file"""
        try:
            # Create new AssetLoader with custom path
            new_asset_loader = AssetLoader(csv_path)

            # Try to load assets
            if new_asset_loader.load_assets():
                # If successful, replace current asset loader
                self.asset_loader = new_asset_loader
                logger.info(f"Successfully loaded assets from {csv_path}")
                return True
            else:
                logger.error(f"Failed to load assets from {csv_path}")
                return False

        except Exception as e:
            logger.error(f"Error loading assets from {csv_path}: {e}")
            return False

    def set_refresh_interval(self, new_interval: int) -> bool:
        """Change the refresh interval (requires restart if engine is running)"""
        try:
            if new_interval < 1 or new_interval > 60:
                logger.error(f"Invalid refresh interval: {new_interval}. Must be between 1-60 seconds")
                return False

            old_interval = self.refresh_interval
            self.refresh_interval = new_interval

            logger.info(f"Refresh interval changed from {old_interval}s to {new_interval}s")

            # If engine is running, notify that restart is needed for change to take effect
            if self.running:
                logger.warning("Engine is running. Refresh interval change will take effect on next restart")

            return True

        except Exception as e:
            logger.error(f"Error setting refresh interval: {e}")
            return False

    # ================================
    # TIME BLOCK VOLUME ANALYSIS
    # ================================

    def _is_time_block_boundary(self, current_time: datetime) -> Optional[int]:
        """
        Check if current time is at a time block boundary.

        Returns:
            Time block number (1-6) if at boundary, None otherwise
        """
        # Extra safety check - ensure current_time is actually a datetime object
        if not isinstance(current_time, datetime):
            logger.error(f"Invalid time object in _is_time_block_boundary: {type(current_time)}")
            return None

        time_obj = current_time.time()

        # Time block boundaries (allowing 1-minute window for processing)
        # Import locally to avoid any namespace issues
        from datetime import time as dt_time
        boundaries = {
            1: (dt_time(10, 14), dt_time(10, 16)),  # Block 1: 9:15-10:15 (check 10:14-10:16)
            2: (dt_time(11, 14), dt_time(11, 16)),  # Block 2: 10:15-11:15 (check 11:14-11:16)
            3: (dt_time(12, 14), dt_time(12, 16)),  # Block 3: 11:15-12:15 (check 12:14-12:16)
            4: (dt_time(13, 14), dt_time(13, 16)),  # Block 4: 12:15-13:15 (check 13:14-13:16)
            5: (dt_time(14, 14), dt_time(14, 16)),  # Block 5: 13:15-14:15 (check 14:14-14:16)
            6: (dt_time(15, 29), dt_time(15, 31))   # Block 6: 14:15-15:30 (check 15:29-15:31)
        }

        for block_num, (start_time, end_time) in boundaries.items():
            if start_time <= time_obj <= end_time:
                return block_num

        return None

    def _process_time_block_analysis(self, current_time: datetime, time_block: int) -> None:
        """
        Process time block volume analysis: store volumes and calculate averages.

        Args:
            current_time: Current timestamp
            time_block: Time block number (1-6)
        """
        try:
            logger.info(f"Processing time block {time_block} analysis at {current_time.isoformat()}")

            # Get all assets from asset loader
            if self.asset_loader.assets_df is None:
                logger.warning("No assets loaded for time block analysis")
                return

            current_date = current_time.date().isoformat()

            # Store volume data for all assets at this time block
            self._store_time_block_volumes(current_date, time_block, current_time)

            # Calculate and store rolling averages for all assets
            self._calculate_and_store_averages(current_date, time_block, current_time)

            logger.info(f"Time block {time_block} analysis completed successfully")

        except Exception as e:
            logger.error(f"Error in time block analysis for block {time_block}: {e}")

    def _store_time_block_volumes(self, current_date: str, time_block: int, current_time: datetime) -> None:
        """Store current cumulative volumes for all assets at this time block."""
        # Time block ranges (block number -> (start_time, end_time))
        # Used to calculate total volume within each hourly block
        time_block_ranges = {
            1: ('09:15:00', '10:15:00'),  # Block 1: 9:15-10:15
            2: ('10:15:00', '11:15:00'),  # Block 2: 10:15-11:15
            3: ('11:15:00', '12:15:00'),  # Block 3: 11:15-12:15
            4: ('12:15:00', '13:15:00'),  # Block 4: 12:15-13:15
            5: ('13:15:00', '14:15:00'),  # Block 5: 13:15-14:15
            6: ('14:15:00', '15:30:00')   # Block 6: 14:15-15:30
        }

        # Get time range for this time block
        start_time, end_time = time_block_ranges.get(time_block, (None, None))
        if not start_time:
            logger.error(f"Invalid time block number: {time_block}")
            return

        try:
            with self.database.get_connection() as conn:
                for _, asset_row in self.asset_loader.assets_df.iterrows():
                    asset_id = int(asset_row['asset_id'])
                    symbol = asset_row['asset_name']

                    # Calculate time block volume by summing interval_volume for this specific time block
                    # NOTE: Despite the misleading name "cumulative_volume" in the table, this actually stores
                    # the TOTAL volume within this time block (sum of all interval_volume values in the block)
                    volume_query = """
                        SELECT COALESCE(SUM(interval_volume), 0)
                        FROM ohlc_data
                        WHERE asset_id = ?
                          AND DATE(timestamp) = ?
                          AND TIME(timestamp) >= ?
                          AND TIME(timestamp) < ?
                    """
                    result = conn.execute(volume_query, [asset_id, current_date, start_time, end_time]).fetchone()
                    cumulative_volume = result[0]  # Total volume for this time block

                    if cumulative_volume == 0:
                        logger.debug(f"No volume data found for asset {asset_id} at time block {time_block}")

                    # Store time block volume (INSERT OR REPLACE for safety)
                    conn.execute("""
                        INSERT OR REPLACE INTO time_block_volumes
                        (date, asset_id, symbol, time_block, cumulative_volume, timestamp)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, [current_date, asset_id, symbol, time_block, cumulative_volume, current_time.isoformat()])

                conn.commit()
                logger.debug(f"Stored time block volumes for {len(self.asset_loader.assets_df)} assets")

        except Exception as e:
            logger.error(f"Error storing time block volumes: {e}")

    def _calculate_and_store_averages(self, current_date: str, time_block: int, current_time: datetime) -> None:
        """Calculate and store 22-day and 100-day rolling averages for all assets."""
        try:
            with self.database.get_connection() as conn:
                for _, asset_row in self.asset_loader.assets_df.iterrows():
                    asset_id = int(asset_row['asset_id'])

                    # Calculate averages for both timeframes
                    for timeframe, days in [('22d', 22), ('100d', 100)]:
                        # Get historical volumes for this asset/time_block
                        history_query = """
                            SELECT cumulative_volume FROM time_block_volumes
                            WHERE asset_id = ? AND time_block = ? AND date < ?
                            ORDER BY date DESC
                            LIMIT ?
                        """
                        results = conn.execute(history_query, [asset_id, time_block, current_date, days]).fetchall()

                        if len(results) >= min(5, days):  # Need at least 5 days of data
                            volumes = [row[0] for row in results]
                            average_volume = sum(volumes) / len(volumes)
                            data_points = len(volumes)

                            # Store the average (INSERT OR REPLACE for safety)
                            conn.execute("""
                                INSERT OR REPLACE INTO time_block_averages
                                (asset_id, time_block, timeframe, average_volume, last_updated, data_points)
                                VALUES (?, ?, ?, ?, ?, ?)
                            """, [asset_id, time_block, timeframe, average_volume, current_time.isoformat(), data_points])
                        else:
                            logger.debug(f"Insufficient data for asset {asset_id}, time_block {time_block}, timeframe {timeframe}: {len(results)} days")

                conn.commit()
                logger.debug(f"Updated rolling averages for time block {time_block}")

        except Exception as e:
            logger.error(f"Error calculating rolling averages: {e}")

# ================================
# MAIN EXECUTION
# ================================

def signal_handler(signum, frame):
    """Handle shutdown signals gracefully"""
    logger.info(f"Received signal {signum} - shutting down gracefully")
    if 'engine' in globals():
        engine.stop()
    sys.exit(0)

def main():
    """Main entry point"""
    # Setup signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        logger.info("=== OHLC Data Collection Engine ===")
        logger.info(f"Configuration:")
        logger.info(f"  - Refresh interval: {REFRESH_INTERVAL_SECONDS} seconds")
        logger.info(f"  - Database path: {DATABASE_PATH}")
        logger.info(f"  - API rate limit: {API_RATE_LIMIT_SECONDS} seconds")
        logger.info(f"  - Data retention: {DATA_RETENTION_DAYS} days")

        # Create and start engine
        global engine
        engine = OHLCEngine()

        if engine.start():
            logger.info("Engine started successfully - press Ctrl+C to stop")

            # Keep main thread alive
            try:
                while engine.running:
                    time.sleep(1)
            except KeyboardInterrupt:
                logger.info("Keyboard interrupt received")
        else:
            logger.error("Failed to start engine")
            return 1

    except Exception as e:
        logger.error(f"Fatal error: {e}")
        return 1
    finally:
        if 'engine' in globals():
            engine.stop()

    return 0

if __name__ == "__main__":
    sys.exit(main())