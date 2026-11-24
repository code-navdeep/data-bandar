"""
Enhanced Dhan Options Data Provider
Clean architecture with standardized schema and efficient data flow
"""

import sqlite3
import pandas as pd
import numpy as np
import logging
import time
from datetime import datetime, timedelta
import os
import requests
import json
import zmq
from contextlib import contextmanager
from typing import Optional, Dict, List, Tuple, Any
from dataclasses import dataclass
from dotenv import load_dotenv

# Load environment variables - override system env vars with .env file
load_dotenv(override=True)

# Setup logging
log_level = os.getenv('LOG_LEVEL', 'INFO')
logging.basicConfig(level=getattr(logging, log_level))
logger = logging.getLogger(__name__)

@dataclass
class AssetConfig:
    """Configuration for an asset"""
    asset_id: int
    asset_name: str
    segment_code: str
    exchange: str
    lot_size: int = None
    number_expiries: int = 1  # Number of expiries to collect for this asset, defaults to 1


class PersonalDhanClient:
    """Enhanced Dhan client with clean architecture and efficient data storage."""
    
    def __init__(self, client_id: str = None, access_token: str = None, db_path: str = None):
        # Initialize request statistics
        self.request_stats = {
            'success_count': 0,
            'error_count': 0,
            'last_error_time': None
        }
        self.last_request_time = 0
        
        # Load from environment variables if not provided
        self.client_id = client_id or os.getenv('DHAN_CLIENT_ID')
        self.access_token = access_token or os.getenv('DHAN_ACCESS_TOKEN')
        self.base_url = os.getenv('DHAN_BASE_URL', 'https://api.dhan.co/v2')
        
        if not self.client_id or not self.access_token:
            raise ValueError("Dhan client_id and access_token must be provided or set in environment variables")
        
        # Database path configuration
        if db_path:
            self.db_path = db_path
        elif os.getenv('DB_PATH'):
            self.db_path = os.getenv('DB_PATH')
        else:
            data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
            os.makedirs(data_dir, exist_ok=True)
            self.db_path = os.path.join(data_dir, "options_database.db")
        
        # Rate limiting configuration from environment
        self.base_rate_limit_delay = float(os.getenv('BASE_RATE_LIMIT_DELAY', '3.0'))
        self.adaptive_delay = self.base_rate_limit_delay
        self.max_retries = int(os.getenv('MAX_RETRIES', '3'))
        
        self._init_database()
        logger.info(f"Enhanced Dhan provider initialized with database at {self.db_path}")

        # Initialize ZMQ publisher for snapshot notifications
        self.zmq_context = zmq.Context()
        self.zmq_publisher = self.zmq_context.socket(zmq.PUB)
        self.zmq_publisher.bind("tcp://127.0.0.1:5555")
        logger.info("ZMQ publisher initialized on tcp://127.0.0.1:5555")
    
    @contextmanager
    def get_db_connection(self):
        """Database connection context manager with optimal settings"""
        conn = None
        try:
            db_timeout = float(os.getenv('DB_TIMEOUT', '30.0'))
            db_cache_size = int(os.getenv('DB_CACHE_SIZE', '-64000'))
            
            conn = sqlite3.connect(self.db_path, timeout=db_timeout)
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.execute(f"PRAGMA cache_size = {db_cache_size}")
            yield conn
        except sqlite3.Error as e:
            logger.error(f"Database error: {e}")
            if conn:
                conn.rollback()
            raise
        finally:
            if conn:
                conn.close()
    
    @contextmanager
    def get_readonly_db_connection(self):
        """Read-only database connection context manager for analytics"""
        conn = None
        try:
            db_timeout = float(os.getenv('DB_TIMEOUT', '30.0'))
            db_cache_size = int(os.getenv('DB_CACHE_SIZE', '-64000'))
            
            # Open in read-only mode to prevent any corruption risk
            conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", 
                                 timeout=db_timeout, uri=True)
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.execute(f"PRAGMA cache_size = {db_cache_size}")
            yield conn
        except sqlite3.Error as e:
            logger.error(f"Read-only database error: {e}")
            raise
        finally:
            if conn:
                conn.close()
    
    def _init_database(self):
        """Initialize database with clean standardized schema - all tables in single execution"""
        with self.get_db_connection() as conn:
            # Execute complete database schema creation in single statement
            conn.executescript('''
                -- Core Business Tables
                CREATE TABLE IF NOT EXISTS options_data (
                    timestamp TIMESTAMP NOT NULL,
                    symbol TEXT NOT NULL,
                    strike REAL NOT NULL,
                    option_type TEXT NOT NULL CHECK (option_type IN ('CE', 'PE')),
                    expiry TEXT NOT NULL,
                    last_price REAL DEFAULT 0,
                    bid_price REAL DEFAULT 0,
                    ask_price REAL DEFAULT 0,
                    volume INTEGER DEFAULT 0,
                    oi INTEGER DEFAULT 0,
                    previous_oi INTEGER DEFAULT 0,
                    oi_change INTEGER DEFAULT 0,
                    iv REAL DEFAULT 0,
                    delta REAL DEFAULT 0,
                    gamma REAL DEFAULT 0,
                    theta REAL DEFAULT 0,
                    vega REAL DEFAULT 0,
                    underlying_price REAL DEFAULT 0,
                    underlying_symbol TEXT NOT NULL,
                    provider TEXT DEFAULT 'dhan',
                    is_highest_call_oi BOOLEAN DEFAULT FALSE,
                    is_second_highest_call_oi BOOLEAN DEFAULT FALSE,
                    is_highest_put_oi BOOLEAN DEFAULT FALSE,
                    is_second_highest_put_oi BOOLEAN DEFAULT FALSE,
                    is_highest_call_oi_change BOOLEAN DEFAULT FALSE,
                    is_highest_put_oi_change BOOLEAN DEFAULT FALSE,
                    UNIQUE(underlying_symbol, strike, option_type, expiry, timestamp),
                    UNIQUE(symbol, timestamp)
                );

                CREATE TABLE IF NOT EXISTS asset_metadata (
                    asset_id INTEGER PRIMARY KEY,
                    asset_name TEXT NOT NULL,
                    segment_code TEXT NOT NULL,
                    exchange TEXT NOT NULL,
                    lot_size INTEGER,
                    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                -- Analysis Tables
                CREATE TABLE IF NOT EXISTS oi_ratios (
                    timestamp TIMESTAMP NOT NULL,
                    underlying_symbol TEXT NOT NULL,
                    expiry TEXT NOT NULL,
                    call_highest_oi INTEGER DEFAULT 0,
                    call_second_highest_oi INTEGER DEFAULT 0,
                    call_ratio REAL DEFAULT 0.0,
                    put_highest_oi INTEGER DEFAULT 0,
                    put_second_highest_oi INTEGER DEFAULT 0,
                    put_ratio REAL DEFAULT 0.0,
                    call_highest_strike REAL DEFAULT 0,
                    call_second_highest_strike REAL DEFAULT 0,
                    put_highest_strike REAL DEFAULT 0,
                    put_second_highest_strike REAL DEFAULT 0,
                    PRIMARY KEY (underlying_symbol, expiry, timestamp)
                );

                -- System Tables
                CREATE TABLE IF NOT EXISTS collection_status (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    asset_id INTEGER NOT NULL,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    status TEXT NOT NULL,
                    error_message TEXT,
                    records_collected INTEGER DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS expiry_cache (
                    asset_id INTEGER NOT NULL,
                    segment_code TEXT NOT NULL,
                    expiry_dates TEXT NOT NULL,
                    cached_timestamp TIMESTAMP NOT NULL,
                    cache_expires_at TIMESTAMP NOT NULL,
                    PRIMARY KEY (asset_id, segment_code)
                );

                -- Optimized Indexes
                CREATE INDEX IF NOT EXISTS idx_options_symbol_timestamp 
                ON options_data(underlying_symbol, timestamp DESC);

                CREATE INDEX IF NOT EXISTS idx_options_expiry_strike 
                ON options_data(underlying_symbol, expiry, strike, option_type);

                CREATE INDEX IF NOT EXISTS idx_collection_status_asset_time 
                ON collection_status(asset_id, timestamp DESC);

                CREATE INDEX IF NOT EXISTS idx_oi_ratios_symbol_timestamp 
                ON oi_ratios(underlying_symbol, timestamp DESC);
            ''')
            
            logger.info("Complete database schema initialized with all tables and indexes")
    
    def load_assets(self, csv_path: str) -> List[AssetConfig]:
        """Load assets from CSV file and store in database"""
        try:
            df = pd.read_csv(csv_path)
            assets = []
            
            for _, row in df.iterrows():
                # Parse number_expiries with fallback logic
                num_expiries = 1  # Default to 1 expiry (latest only)
                if 'number_expiries' in df.columns:
                    expiry_value = row.get('number_expiries')
                    if pd.notna(expiry_value) and expiry_value != '':
                        try:
                            num_expiries = max(1, int(expiry_value))  # Ensure at least 1
                        except (ValueError, TypeError):
                            logger.warning(f"Invalid number_expiries value '{expiry_value}' for asset {row['asset_name']}, using default 1")
                            num_expiries = 1
                
                asset = AssetConfig(
                    asset_id=int(row['asset_id']),
                    asset_name=str(row['asset_name']),
                    segment_code=str(row['segment_code']),
                    exchange=str(row['exchange']),
                    lot_size=int(row.get('lot_size', 0)) if pd.notna(row.get('lot_size')) else None,
                    number_expiries=num_expiries
                )
                assets.append(asset)
            
            # Store asset metadata - preserve existing data
            with self.get_db_connection() as conn:
                asset_data = [
                    (asset.asset_id, asset.asset_name, asset.segment_code, 
                     asset.exchange, asset.lot_size, datetime.now())
                    for asset in assets
                ]
                conn.executemany('''
                    INSERT OR IGNORE INTO asset_metadata 
                    (asset_id, asset_name, segment_code, exchange, lot_size, last_updated)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', asset_data)
                conn.commit()
            
            logger.info(f"Loaded and stored {len(assets)} assets from {csv_path}")
            return assets
            
        except Exception as e:
            logger.error(f"Error loading assets from {csv_path}: {e}")
            return []
    
    def get_loaded_assets(self) -> List[AssetConfig]:
        """Retrieve loaded assets from database"""
        try:
            with self.get_db_connection() as conn:
                query = "SELECT asset_id, asset_name, segment_code, exchange, lot_size FROM asset_metadata"
                cursor = conn.execute(query)
                assets = []
                
                for row in cursor.fetchall():
                    asset = AssetConfig(
                        asset_id=row[0],
                        asset_name=row[1],
                        segment_code=row[2],
                        exchange=row[3],
                        lot_size=row[4]
                    )
                    assets.append(asset)
                
                logger.info(f"Retrieved {len(assets)} assets from database")
                return assets
                
        except Exception as e:
            logger.error(f"Error retrieving assets: {e}")
            return []
    
    def collect_asset(self, asset_id: int, segment_code: str, num_expiries: int = 1) -> bool:
        """Collect option data for a single asset with clean single-transaction approach"""
        try:
            logger.info(f"Collecting data for asset {asset_id} in segment {segment_code}")
            
            # Get expiry list first
            expiry_list = self.get_expiry_list(asset_id, segment_code)
            if not expiry_list:
                self._log_collection_status(asset_id, 'failed', 'No expiry dates available', 0)
                return False
            
            # Collect data for specified number of expiries
            expiries_to_collect = expiry_list[:num_expiries]
            all_dfs = []
            
            for expiry in expiries_to_collect:
                df = self.get_option_chain(expiry, asset_id, segment_code)
                if not df.empty:
                    all_dfs.append(df)
            
            if not all_dfs:
                self._log_collection_status(asset_id, 'no_data', 'No option chain data collected', 0)
                return False
            
            # Combine all DataFrames
            df = pd.concat(all_dfs, ignore_index=True)

            # ========================================
            # OI PROCESSING SYSTEM INTEGRATION POINT
            # Calculate OI markings on filtered strikes (±20 from ATM) then merge back to full dataset
            # ========================================

            # Create filtered copy for OI processing context (±20 strikes from ATM)
            oi_df = self._filter_strikes_for_oi_processing(df, strike_range=20)

            # Process OI markings on the filtered, meaningful strikes only
            oi_df = self._mark_oi_leaders_on_filtered_data(oi_df)

            # Calculate and store OI ratios from the marked data
            self._calculate_and_store_oi_ratios(oi_df)

            # Merge OI markings back to the complete dataset
            # Strikes outside ±20 range get False markings, strikes within range get calculated markings
            df = self._merge_oi_results_back_to_full_dataset(df, oi_df)

            # Single transaction: direct DataFrame storage
            with self.get_db_connection() as conn:
                try:
                    # Store DataFrame directly - matches database schema exactly
                    df.to_sql('options_data', conn, if_exists='append', index=False)
                    records_stored = len(df)
                except sqlite3.IntegrityError as e:
                    logger.warning(f"Constraint violation during insertion: {e}")
                    records_stored = 0
                
                # Log collection status
                conn.execute('''
                    INSERT INTO collection_status (asset_id, timestamp, status, error_message, records_collected)
                    VALUES (?, ?, ?, ?, ?)
                ''', (asset_id, datetime.now(), 'success', None, records_stored))
                
                conn.commit()

            # Publish ZMQ event for each unique (underlying_symbol, expiry, timestamp) combination
            if records_stored > 0:
                # Extract unique combinations from the stored DataFrame
                unique_snapshots = df[['underlying_symbol', 'expiry', 'timestamp']].drop_duplicates()

                for _, row in unique_snapshots.iterrows():
                    event_message = json.dumps({
                        'event': 'snapshot_ready',
                        'underlying_symbol': row['underlying_symbol'],
                        'expiry': row['expiry'],
                        'timestamp': row['timestamp']
                    })
                    self.zmq_publisher.send_string(event_message)
                    logger.debug(f"ZMQ: Published snapshot_ready for {row['underlying_symbol']} expiry {row['expiry']}")

                logger.info(f"Published {len(unique_snapshots)} ZMQ snapshot_ready events")

            logger.info(f"Successfully collected and stored {records_stored} records for asset {asset_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error collecting asset {asset_id}: {e}")
            self._log_collection_status(asset_id, 'failed', str(e), 0)
            return False
    
    def _log_collection_status(self, asset_id: int, status: str, error_message: str = None, records_count: int = 0):
        """Internal method to log collection status"""
        try:
            with self.get_db_connection() as conn:
                conn.execute('''
                    INSERT INTO collection_status (asset_id, timestamp, status, error_message, records_collected)
                    VALUES (?, ?, ?, ?, ?)
                ''', (asset_id, datetime.now(), status, error_message, records_count))
                conn.commit()
        except Exception as e:
            logger.error(f"Error logging collection status: {e}")
    
    def log_request_success(self):
        """Log successful API request"""
        self.request_stats['success_count'] += 1
    
    def log_request_error(self):
        """Log failed API request"""
        self.request_stats['error_count'] += 1
        self.request_stats['last_error_time'] = datetime.now()
    
    def get_success_rate(self) -> float:
        """Calculate API request success rate"""
        total = self.request_stats['success_count'] + self.request_stats['error_count']
        return (self.request_stats['success_count'] / total * 100) if total > 0 else 0.0
    
    def is_healthy(self) -> bool:
        """Check if provider is healthy based on success rate"""
        return self.get_success_rate() > 50.0
    
    @staticmethod
    def get_standard_columns() -> List[str]:
        """Get list of standard columns for options data"""
        return [
            'timestamp', 'symbol', 'strike', 'option_type', 'expiry', 'last_price',
            'bid_price', 'ask_price', 'volume', 'oi', 'previous_oi', 
            'oi_change', 'iv', 'delta', 'gamma', 'theta', 'vega',
            'underlying_price', 'underlying_symbol', 'provider'
        ]
    
    def validate_output_schema(self, df: pd.DataFrame) -> Tuple[bool, str]:
        """Validate DataFrame schema matches expected format"""
        if df.empty:
            return True, "Empty DataFrame is valid"
        
        required_columns = self.get_standard_columns()
        missing_columns = [col for col in required_columns if col not in df.columns]
        
        if missing_columns:
            return False, f"Missing required columns: {missing_columns}"
        
        if 'option_type' in df.columns:
            invalid_types = df[~df['option_type'].isin(['CE', 'PE'])]['option_type'].unique()
            if len(invalid_types) > 0:
                return False, f"Invalid option_type values: {invalid_types}"
        
        return True, "Schema validation passed"
    
    def _adaptive_rate_limit(self):
        """Adaptive rate limiting based on success/error ratio"""
        current_time = time.time()
        time_since_last_request = current_time - self.last_request_time
        
        if self.request_stats['error_count'] > 0:
            error_ratio = self.request_stats['error_count'] / max(1, self.request_stats['success_count'])
            self.adaptive_delay = min(10.0, self.base_rate_limit_delay * (1 + error_ratio))
        else:
            self.adaptive_delay = max(1.0, self.adaptive_delay * 0.9)
        
        if time_since_last_request < self.adaptive_delay:
            sleep_time = self.adaptive_delay - time_since_last_request
            logger.debug(f"Adaptive rate limiting: sleeping for {sleep_time:.1f}s")
            time.sleep(sleep_time)
        
        self.last_request_time = time.time()
    
    def _make_api_request(self, endpoint: str, payload: Dict, retries: int = None) -> Optional[Dict]:
        """Make API request with robust error handling and rate limiting"""
        if retries is None:
            retries = self.max_retries
            
        headers = {
            'access-token': self.access_token,
            'client-id': self.client_id,
            'Content-Type': 'application/json'
        }
        
        url = f"{self.base_url}{endpoint}"
        api_timeout = float(os.getenv('API_TIMEOUT', '30'))
        
        for attempt in range(retries + 1):
            try:
                self._adaptive_rate_limit()
                
                logger.debug(f"API request attempt {attempt + 1}: {endpoint}")
                response = requests.post(url, headers=headers, json=payload, timeout=api_timeout)
                
                if response.status_code == 429:
                    logger.warning("Rate limit hit, increasing delay")
                    self.adaptive_delay = min(10.0, self.adaptive_delay * 2)
                    self.log_request_error()
                    
                    if attempt < retries:
                        time.sleep(self.adaptive_delay)
                        continue
                    else:
                        raise requests.exceptions.RequestException("Rate limit exceeded")
                
                response.raise_for_status()
                result = response.json()
                
                self.log_request_success()
                logger.debug(f"API request successful: {endpoint}")
                
                return result
                
            except requests.exceptions.RequestException as e:
                self.log_request_error()
                logger.error(f"API request failed (attempt {attempt + 1}): {e}")
                
                if attempt < retries:
                    wait_time = (attempt + 1) * 2
                    logger.info(f"Retrying in {wait_time}s...")
                    time.sleep(wait_time)
                else:
                    logger.error(f"API request failed after {retries + 1} attempts: {endpoint}")
                    return None
        
        return None
    
    def validate_input(self, underlying_scrip: int, underlying_seg: str, expiry: str) -> Tuple[bool, str]:
        """Validate input parameters"""
        if not isinstance(underlying_scrip, int) or underlying_scrip <= 0:
            return False, f"Invalid asset ID: {underlying_scrip}"
        
        valid_segments = ['IDX_I', 'NSE_EQ', 'MCX_COMM', 'CME', 'CRYPTO']
        if underlying_seg not in valid_segments:
            return False, f"Invalid segment: {underlying_seg}. Must be one of {valid_segments}"
        
        if expiry:
            try:
                expiry_date = datetime.strptime(expiry, '%Y-%m-%d')
                if expiry_date.date() < datetime.now().date():
                    return False, f"Expiry date {expiry} is in the past"
            except ValueError:
                return False, f"Invalid expiry date format: {expiry}. Use YYYY-MM-DD"
        
        return True, "Valid"
    
    def get_expiry_list(self, underlying_scrip: int, underlying_seg: str) -> List[str]:
        """Get list of expiry dates for an asset with caching"""
        if not isinstance(underlying_scrip, int) or underlying_scrip <= 0:
            logger.error(f"Invalid asset ID: {underlying_scrip}")
            return []
        
        valid_segments = ['IDX_I', 'NSE_EQ', 'MCX_COMM', 'CME', 'CRYPTO']
        if underlying_seg not in valid_segments:
            logger.error(f"Invalid segment: {underlying_seg}. Must be one of {valid_segments}")
            return []
        
        try:
            # Check cache first - expires next Tuesday 4 PM
            with self.get_db_connection() as conn:
                cursor = conn.execute('''
                    SELECT expiry_dates, cache_expires_at 
                    FROM expiry_cache 
                    WHERE asset_id = ? AND segment_code = ?
                ''', (underlying_scrip, underlying_seg))
                
                cached_result = cursor.fetchone()
                
                if cached_result:
                    expiry_dates_json, cache_expires_at = cached_result
                    expires_at = datetime.fromisoformat(cache_expires_at)
                    
                    if datetime.now() < expires_at:
                        # Cache is valid, return cached data
                        expiry_list = json.loads(expiry_dates_json)
                        logger.debug(f"Using cached expiry list for asset {underlying_scrip} (expires Tuesday 4 PM)")
                        return expiry_list
            
            # Cache miss or expired - fetch from API
            payload = {
                "UnderlyingScrip": underlying_scrip,
                "UnderlyingSeg": underlying_seg
            }
            
            response = self._make_api_request("/optionchain/expirylist", payload)
            
            if response and response.get('status') == 'success':
                expiry_list = response.get('data', [])
                logger.info(f"Retrieved {len(expiry_list)} expiry dates for asset {underlying_scrip}")
                
                # Update cache with fresh data - expires next Tuesday 4 PM
                cache_expires_at = self._calculate_next_tuesday_4pm()
                with self.get_db_connection() as conn:
                    conn.execute('''
                        INSERT OR REPLACE INTO expiry_cache 
                        (asset_id, segment_code, expiry_dates, cached_timestamp, cache_expires_at)
                        VALUES (?, ?, ?, ?, ?)
                    ''', (underlying_scrip, underlying_seg, json.dumps(expiry_list), 
                          datetime.now().isoformat(), cache_expires_at.isoformat()))
                    conn.commit()
                
                return expiry_list
            else:
                logger.error(f"API failed to get expiry list: {response}")
                
                # Extend existing cache on API failure - keep until next Tuesday 4 PM
                with self.get_db_connection() as conn:
                    cursor = conn.execute('''
                        SELECT expiry_dates FROM expiry_cache 
                        WHERE asset_id = ? AND segment_code = ?
                    ''', (underlying_scrip, underlying_seg))
                    
                    cached_result = cursor.fetchone()
                    
                    if cached_result:
                        # Extend existing cache until next Tuesday 4 PM
                        new_expires_at = self._calculate_next_tuesday_4pm()
                        conn.execute('''
                            UPDATE expiry_cache 
                            SET cache_expires_at = ? 
                            WHERE asset_id = ? AND segment_code = ?
                        ''', (new_expires_at.isoformat(), underlying_scrip, underlying_seg))
                        conn.commit()
                        
                        expiry_list = json.loads(cached_result[0])
                        logger.warning(f"API failed, extended cache for asset {underlying_scrip} until Tuesday 4 PM")
                        return expiry_list
                
                return []
                
        except Exception as e:
            logger.error(f"Error getting expiry list: {e}")
            return []
    
    def fetch_option_chain(self, expiry: str, underlying_scrip: int, underlying_seg: str) -> Optional[Dict]:
        """Fetch raw option chain data from API"""
        is_valid, error_msg = self.validate_input(underlying_scrip, underlying_seg, expiry)
        if not is_valid:
            logger.error(f"Input validation failed: {error_msg}")
            return None
        
        try:
            payload = {
                "UnderlyingScrip": underlying_scrip,
                "UnderlyingSeg": underlying_seg,
                "Expiry": expiry
            }
            
            logger.info(f"Fetching option chain: Asset={underlying_scrip}, Segment={underlying_seg}, Expiry={expiry}")
            response = self._make_api_request("/optionchain", payload)
            
            if response and response.get('data'):
                logger.info(f"Successfully fetched option chain data for {underlying_scrip}")
                return response['data']
            else:
                logger.error(f"Failed to fetch option chain: {response}")
                return None
                
        except Exception as e:
            logger.error(f"Error fetching option chain: {e}")
            return None
    
    def parse_option_chain(self, option_chain_data: Dict, underlying_scrip: int, underlying_seg: str, expiry: str) -> pd.DataFrame:
        """Parse raw option chain data into standardized DataFrame"""
        if not option_chain_data:
            logger.warning("No option chain data to parse")
            return pd.DataFrame()
        
        # Validate expiry before processing
        if not expiry or expiry.strip() == "":
            logger.error("Cannot create valid symbols without expiry date")
            return pd.DataFrame()
        
        try:
            underlying_price = option_chain_data.get('last_price', 0)
            option_chain = option_chain_data.get('oc', {})
            
            if not option_chain:
                logger.warning("No option chain data in response")
                return pd.DataFrame()
            
            parsed_data = []
            timestamp = datetime.now().isoformat()
            underlying_symbol = f"asset_{underlying_scrip}_{underlying_seg}"
            
            for strike_str, strike_data in option_chain.items():
                try:
                    strike = float(strike_str)
                except (ValueError, TypeError):
                    logger.warning(f"Invalid strike price: {strike_str}")
                    continue
                
                # Process Call option
                if 'ce' in strike_data and strike_data['ce']:
                    ce_data = strike_data['ce']
                    greeks = ce_data.get('greeks', {})
                    
                    current_oi = ce_data.get('oi', 0) or 0
                    previous_oi = ce_data.get('previous_oi', 0) or 0
                    oi_change = current_oi - previous_oi
                    
                    symbol = f"{underlying_symbol}_CE_{strike}_{expiry}"
                    if any(char in symbol for char in [' ', '\t', '\n', '\r']):
                        logger.warning(f"Invalid symbol constructed: {symbol}")
                        continue
                    
                    parsed_data.append({
                        'timestamp': timestamp,
                        'symbol': symbol,
                        'strike': strike,
                        'option_type': 'CE',
                        'expiry': expiry,
                        'last_price': ce_data.get('last_price', 0) or 0,
                        'bid_price': ce_data.get('top_bid_price', 0) or 0,
                        'ask_price': ce_data.get('top_ask_price', 0) or 0,
                        'volume': ce_data.get('volume', 0) or 0,
                        'oi': current_oi,
                        'previous_oi': previous_oi,
                        'oi_change': oi_change,
                        'iv': ce_data.get('implied_volatility', 0) or 0,
                        'delta': greeks.get('delta', 0) or 0,
                        'gamma': greeks.get('gamma', 0) or 0,
                        'theta': greeks.get('theta', 0) or 0,
                        'vega': greeks.get('vega', 0) or 0,
                        'underlying_price': underlying_price,
                        'underlying_symbol': underlying_symbol,
                        'provider': 'dhan'
                    })
                
                # Process Put option
                if 'pe' in strike_data and strike_data['pe']:
                    pe_data = strike_data['pe']
                    greeks = pe_data.get('greeks', {})
                    
                    current_oi = pe_data.get('oi', 0) or 0
                    previous_oi = pe_data.get('previous_oi', 0) or 0
                    oi_change = current_oi - previous_oi
                    
                    symbol = f"{underlying_symbol}_PE_{strike}_{expiry}"
                    if any(char in symbol for char in [' ', '\t', '\n', '\r']):
                        logger.warning(f"Invalid symbol constructed: {symbol}")
                        continue
                    
                    parsed_data.append({
                        'timestamp': timestamp,
                        'symbol': symbol,
                        'strike': strike,
                        'option_type': 'PE',
                        'expiry': expiry,
                        'last_price': pe_data.get('last_price', 0) or 0,
                        'bid_price': pe_data.get('top_bid_price', 0) or 0,
                        'ask_price': pe_data.get('top_ask_price', 0) or 0,
                        'volume': pe_data.get('volume', 0) or 0,
                        'oi': current_oi,
                        'previous_oi': previous_oi,
                        'oi_change': oi_change,
                        'iv': pe_data.get('implied_volatility', 0) or 0,
                        'delta': greeks.get('delta', 0) or 0,
                        'gamma': greeks.get('gamma', 0) or 0,
                        'theta': greeks.get('theta', 0) or 0,
                        'vega': greeks.get('vega', 0) or 0,
                        'underlying_price': underlying_price,
                        'underlying_symbol': underlying_symbol,
                        'provider': 'dhan'
                    })
            
            if not parsed_data:
                logger.warning("No valid option data parsed")
                return pd.DataFrame()
            
            df = pd.DataFrame(parsed_data)
            logger.info(f"Parsed {len(df)} option records for asset {underlying_scrip}")
            
            return df
            
        except Exception as e:
            logger.error(f"Error parsing option chain: {e}")
            return pd.DataFrame()
    
    def get_option_chain(self, expiry: str, underlying_scrip: int, underlying_seg: str) -> pd.DataFrame:
        """Get standardized option chain DataFrame - expiry is required parameter"""
        try:
            # Fetch raw data with explicit expiry
            option_chain_data = self.fetch_option_chain(expiry, underlying_scrip, underlying_seg)
            if not option_chain_data:
                return pd.DataFrame()
            
            # Parse into standardized format
            df = self.parse_option_chain(option_chain_data, underlying_scrip, underlying_seg, expiry)
            
            return df
            
        except Exception as e:
            logger.error(f"Error in get_option_chain: {e}")
            return pd.DataFrame()
    
    def get_historical_data(self, underlying_symbol: str = None, expiry: str = None, 
                           start_date: str = None, end_date: str = None) -> pd.DataFrame:
        """Get historical options data with filtering parameters"""
        try:
            # Input validation
            if start_date:
                try:
                    datetime.strptime(start_date, '%Y-%m-%d')
                except ValueError:
                    logger.error(f"Invalid start_date format: {start_date}. Use YYYY-MM-DD")
                    return pd.DataFrame()
            
            if end_date:
                try:
                    datetime.strptime(end_date, '%Y-%m-%d')
                except ValueError:
                    logger.error(f"Invalid end_date format: {end_date}. Use YYYY-MM-DD")
                    return pd.DataFrame()
            
            # Build query dynamically based on parameters
            query = """
                SELECT timestamp, symbol, strike, option_type, expiry, last_price, bid_price, ask_price, 
                       volume, oi, previous_oi, oi_change, iv, delta, gamma, theta, vega, 
                       underlying_price, underlying_symbol, provider
                FROM options_data
            """
            
            conditions = []
            params = []
            
            if underlying_symbol:
                conditions.append("underlying_symbol = ?")
                params.append(underlying_symbol)
            
            if expiry:
                conditions.append("expiry = ?")
                params.append(expiry)
            
            if start_date:
                conditions.append("DATE(timestamp) >= ?")
                params.append(start_date)
            
            if end_date:
                conditions.append("DATE(timestamp) <= ?")
                params.append(end_date)
            
            if conditions:
                query += " WHERE " + " AND ".join(conditions)
            
            query += " ORDER BY timestamp DESC, strike, option_type"
            
            # Execute with read-only connection
            with self.get_readonly_db_connection() as conn:
                df = pd.read_sql_query(query, conn, params=params)
                
            logger.info(f"Retrieved {len(df)} historical records")
            return df
            
        except Exception as e:
            logger.error(f"Error getting historical data: {e}")
            return pd.DataFrame()
    
    def get_latest_data(self, underlying_symbol: str = None, expiry: str = None) -> pd.DataFrame:
        """Get latest data for specific underlying symbol and expiry"""
        try:
            with self.get_db_connection() as conn:
                query = """
                    WITH latest_timestamp AS (
                        SELECT MAX(timestamp) as max_ts 
                        FROM options_data 
                        WHERE underlying_symbol = ? AND expiry = ?
                    )
                    SELECT timestamp, symbol, strike, option_type, expiry, last_price, bid_price, ask_price, 
                           volume, oi, previous_oi, oi_change, iv, delta, gamma, theta, vega, 
                           underlying_price, underlying_symbol, provider
                    FROM options_data 
                    WHERE underlying_symbol = ? AND expiry = ? 
                    AND timestamp = (SELECT max_ts FROM latest_timestamp)
                    ORDER BY strike, option_type
                """
                df = pd.read_sql_query(query, conn, params=[underlying_symbol, expiry, underlying_symbol, expiry])
                
                logger.info(f"Retrieved {len(df)} latest records")
                return df
                
        except Exception as e:
            logger.error(f"Error getting latest data: {e}")
            return pd.DataFrame()
    
    def get_stats(self) -> Dict:
        """Get comprehensive statistics"""
        try:
            with self.get_db_connection() as conn:
                db_stats = {}
                
                # Options data stats
                result = conn.execute("SELECT COUNT(*) FROM options_data").fetchone()
                db_stats['total_records'] = result[0] if result else 0
                
                result = conn.execute("SELECT COUNT(DISTINCT underlying_symbol) FROM options_data").fetchone()
                db_stats['unique_assets'] = result[0] if result else 0
                
                result = conn.execute("SELECT MIN(timestamp), MAX(timestamp) FROM options_data").fetchone()
                if result and result[0]:
                    db_stats['earliest_data'] = result[0]
                    db_stats['latest_data'] = result[1]
                
                # Asset metadata stats
                result = conn.execute("SELECT COUNT(*) FROM asset_metadata").fetchone()
                db_stats['loaded_assets'] = result[0] if result else 0
                
                # Collection status stats
                result = conn.execute("""
                    SELECT status, COUNT(*) FROM collection_status 
                    GROUP BY status
                """).fetchall()
                db_stats['collection_status'] = dict(result) if result else {}
                
                stats = {
                    'database': db_stats,
                    'api_requests': self.request_stats.copy(),
                    'rate_limiting': {
                        'current_delay': self.adaptive_delay,
                        'base_delay': self.base_rate_limit_delay,
                        'success_rate': self.get_success_rate()
                    },
                    'configuration': {
                        'db_path': self.db_path,
                        'max_retries': self.max_retries
                    }
                }
                
                return stats
                
        except Exception as e:
            logger.error(f"Error getting stats: {e}")
            return {}
    
    def get_credentials(self) -> Dict[str, Any]:
        """Get provider credentials and configuration info"""
        return {
            'provider': 'dhan',
            'client_id': self.client_id,
            'has_access_token': bool(self.access_token),
            'base_url': self.base_url,
            'db_path': self.db_path,
            'base_rate_limit_delay': self.base_rate_limit_delay
        }
    
    def get_expiry_cache_status(self) -> Dict[str, Any]:
        """Get current expiry cache status for UI display"""
        try:
            with self.get_db_connection() as conn:
                # Get cache statistics
                cursor = conn.execute('''
                    SELECT 
                        COUNT(*) as total_entries,
                        MIN(cached_timestamp) as oldest_entry,
                        MAX(cached_timestamp) as newest_entry,
                        MIN(cache_expires_at) as next_expiry
                    FROM expiry_cache
                ''')
                
                stats = cursor.fetchone()
                
                if stats and stats[0] > 0:
                    total_entries, oldest_entry, newest_entry, next_expiry = stats
                    
                    # Calculate next Tuesday 4 PM for reference
                    next_tuesday_4pm = self._calculate_next_tuesday_4pm()
                    
                    return {
                        'total_entries': total_entries,
                        'oldest_entry': oldest_entry,
                        'newest_entry': newest_entry,
                        'next_expiry': next_expiry,
                        'expires_tuesday_4pm': next_tuesday_4pm.isoformat(),
                        'cache_valid': datetime.now() < datetime.fromisoformat(next_expiry) if next_expiry else False,
                        'status': 'healthy' if total_entries > 0 else 'empty'
                    }
                else:
                    return {
                        'total_entries': 0,
                        'status': 'empty',
                        'expires_tuesday_4pm': self._calculate_next_tuesday_4pm().isoformat(),
                        'message': 'No entries in expiry cache'
                    }
                    
        except Exception as e:
            logger.error(f"Error getting cache status: {e}")
            return {'error': str(e), 'status': 'error'}
    
    def get_data_availability_status(self, underlying_symbol: str = None) -> Dict[str, Any]:
        """
        Get data availability status for advanced analytics.
        
        Args:
            underlying_symbol: Asset symbol to check (None = all assets)
            
        Returns:
            Data availability information including expiries, date ranges, freshness
        """
        try:
            with self.get_readonly_db_connection() as conn:
                if underlying_symbol:
                    # Asset-specific availability
                    query = """
                        SELECT 
                            expiry,
                            COUNT(*) as record_count,
                            MIN(timestamp) as earliest_timestamp,
                            MAX(timestamp) as latest_timestamp,
                            COUNT(DISTINCT strike) as strike_count
                        FROM options_data 
                        WHERE underlying_symbol = ?
                        GROUP BY expiry
                        ORDER BY expiry
                    """
                    cursor = conn.execute(query, [underlying_symbol])
                    expiry_data = []
                    
                    for row in cursor.fetchall():
                        expiry_data.append({
                            'expiry': row[0],
                            'record_count': row[1],
                            'earliest_timestamp': row[2],
                            'latest_timestamp': row[3],
                            'strike_count': row[4],
                            'data_quality': 'complete' if row[1] > 20 else 'partial'
                        })
                    
                    # Overall asset status
                    asset_query = """
                        SELECT 
                            COUNT(*) as total_records,
                            COUNT(DISTINCT expiry) as expiry_count,
                            MIN(timestamp) as earliest_data,
                            MAX(timestamp) as latest_data
                        FROM options_data 
                        WHERE underlying_symbol = ?
                    """
                    cursor = conn.execute(asset_query, [underlying_symbol])
                    asset_row = cursor.fetchone()
                    
                    return {
                        'underlying_symbol': underlying_symbol,
                        'expiry_data': expiry_data,
                        'total_records': asset_row[0] if asset_row else 0,
                        'expiry_count': asset_row[1] if asset_row else 0,
                        'earliest_data': asset_row[2] if asset_row else None,
                        'latest_data': asset_row[3] if asset_row else None,
                        'data_freshness_minutes': self._calculate_data_age_minutes(asset_row[3]) if asset_row and asset_row[3] else None,
                        'analysis_capability': self._assess_analysis_capability(len(expiry_data))
                    }
                else:
                    # Summary across all assets
                    summary_query = """
                        SELECT 
                            underlying_symbol,
                            COUNT(DISTINCT expiry) as expiry_count,
                            COUNT(*) as record_count,
                            MAX(timestamp) as latest_timestamp
                        FROM options_data 
                        GROUP BY underlying_symbol
                        ORDER BY latest_timestamp DESC
                    """
                    cursor = conn.execute(summary_query)
                    asset_summaries = []
                    
                    for row in cursor.fetchall():
                        asset_summaries.append({
                            'underlying_symbol': row[0],
                            'expiry_count': row[1],
                            'record_count': row[2],
                            'latest_timestamp': row[3],
                            'data_freshness_minutes': self._calculate_data_age_minutes(row[3]),
                            'analysis_capability': self._assess_analysis_capability(row[1])
                        })
                    
                    return {
                        'summary_type': 'all_assets',
                        'asset_summaries': asset_summaries,
                        'total_assets': len(asset_summaries),
                        'analysis_ready_assets': len([a for a in asset_summaries if a['analysis_capability'] in ['full', 'partial']])
                    }
                    
        except Exception as e:
            logger.error(f"Error getting data availability status: {e}")
            return {'error': str(e)}
    
    def _calculate_data_age_minutes(self, timestamp_str: str) -> float:
        """Calculate age of data in minutes"""
        try:
            if timestamp_str:
                data_time = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
                now = datetime.now()
                age_seconds = (now - data_time).total_seconds()
                return age_seconds / 60.0
            return 0.0
        except Exception:
            return 0.0
    
    def _assess_analysis_capability(self, expiry_count: int) -> str:
        """Assess what level of analysis is possible"""
        if expiry_count >= 5:
            return 'full'
        elif expiry_count >= 2:
            return 'partial'
        elif expiry_count >= 1:
            return 'basic'
        else:
            return 'none'
    
    def _calculate_next_tuesday_4pm(self) -> datetime:
        """Calculate next Tuesday 4 PM for cache expiry"""
        now = datetime.now()
        
        # Find next Tuesday (Monday=0, Tuesday=1, ..., Sunday=6)
        days_ahead = (1 - now.weekday()) % 7  # Tuesday is 1
        
        if days_ahead == 0:  # Today is Tuesday
            # Check if it's past 4 PM
            tuesday_4pm_today = now.replace(hour=16, minute=0, second=0, microsecond=0)
            if now >= tuesday_4pm_today:
                days_ahead = 7  # Next Tuesday
            else:
                days_ahead = 0  # Today at 4 PM
        
        next_tuesday = now + timedelta(days=days_ahead)
        next_tuesday_4pm = next_tuesday.replace(hour=16, minute=0, second=0, microsecond=0)
        
        return next_tuesday_4pm
    
    def clear_expiry_cache(self) -> Dict[str, Any]:
        """Clear entire expiry cache - forces fresh API calls for all assets"""
        try:
            with self.get_db_connection() as conn:
                # Count entries before clearing
                cursor = conn.execute('SELECT COUNT(*) FROM expiry_cache')
                total_count = cursor.fetchone()[0]
                
                # Clear entire cache
                cursor = conn.execute('DELETE FROM expiry_cache')
                deleted_count = cursor.rowcount
                conn.commit()
                
                logger.info(f"Cleared entire expiry cache: removed {deleted_count} entries")
                
                return {
                    'total_entries_found': total_count,
                    'entries_deleted': deleted_count,
                    'cleared_timestamp': datetime.now().isoformat(),
                    'message': f'Cleared {deleted_count} cached expiry entries'
                }
                
        except Exception as e:
            logger.error(f"Error clearing expiry cache: {e}")
            return {'error': str(e)}
    
    def refresh_expiry_cache(self) -> Dict[str, Any]:
        """Refresh expiry cache for all loaded assets - simplified version"""
        try:
            with self.get_db_connection() as conn:
                cursor = conn.execute("SELECT DISTINCT asset_id, segment_code FROM asset_metadata")
                assets = cursor.fetchall()
            
            if not assets:
                logger.warning("No assets found in metadata table")
                return {'total_assets': 0, 'success_count': 0, 'error_count': 0, 'errors': []}
            
            success_count = 0
            error_count = 0
            errors = []
            
            for asset_id, segment_code in assets:
                try:
                    # Force refresh by calling get_expiry_list with fresh API call
                    # First clear this specific asset's cache
                    with self.get_db_connection() as conn:
                        conn.execute('''
                            DELETE FROM expiry_cache 
                            WHERE asset_id = ? AND segment_code = ?
                        ''', (asset_id, segment_code))
                        conn.commit()
                    
                    # Now get fresh data (will automatically cache with Tuesday 4PM expiry)
                    expiry_list = self.get_expiry_list(asset_id, segment_code)
                    if expiry_list:
                        success_count += 1
                        logger.debug(f"Refreshed expiry cache for asset {asset_id}:{segment_code}")
                    else:
                        error_count += 1
                        errors.append(f"Asset {asset_id}:{segment_code} - no expiry data")
                        
                except Exception as e:
                    error_count += 1
                    errors.append(f"Asset {asset_id}:{segment_code} - {str(e)}")
                    logger.error(f"Error refreshing expiry cache for {asset_id}:{segment_code}: {e}")
            
            logger.info(f"Expiry cache refresh completed: {success_count} success, {error_count} errors")
            
            return {
                'total_assets': len(assets),
                'success_count': success_count,
                'error_count': error_count,
                'errors': errors,
                'refresh_timestamp': datetime.now().isoformat(),
                'cache_expires': self._calculate_next_tuesday_4pm().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error in expiry cache refresh: {e}")
            return {'error': str(e)}

    def _filter_strikes_for_oi_processing(self, df: pd.DataFrame, strike_range: int = 20) -> pd.DataFrame:
        """
        Filter strikes to meaningful range for OI calculations ONLY

        OI PROCESSING SYSTEM - Context-Aware Strike Filtering

        Uses same logic as dashboard filter but hardcoded to ±20 strikes around ATM.
        This ensures OI calculations are done on relevant strikes only,
        while still storing ALL strikes in the database.

        Args:
            df: Full options DataFrame
            strike_range: Number of strikes above/below ATM (hardcoded to 20)

        Returns:
            Filtered DataFrame containing only strikes around ATM for OI context
        """
        if df.empty:
            logger.debug("OI PROCESSING: Empty DataFrame passed to strike filter - returning unchanged")
            return df

        try:
            # Get underlying price for ATM calculation
            underlying_price = df['underlying_price'].iloc[0] if len(df) > 0 else 0
            if underlying_price <= 0:
                logger.warning("OI PROCESSING: No valid underlying price for strike filtering - returning full DataFrame")
                return df

            # Get unique strikes and sort them (same as dashboard logic)
            strikes = sorted(df['strike'].unique())
            if len(strikes) == 0:
                logger.warning("OI PROCESSING: No strikes found for filtering - returning original DataFrame")
                return df

            # Find bracket strike (first strike >= underlying_price) - same as dashboard
            bracket_index = None
            for i, strike in enumerate(strikes):
                if strike >= underlying_price:
                    bracket_index = i
                    break

            # If no strike >= underlying_price, use the highest strike
            if bracket_index is None:
                bracket_index = len(strikes) - 1

            # Calculate range: ±20 strikes (hardcoded for OI context)
            start_index = max(0, bracket_index - strike_range)
            end_index = min(len(strikes), bracket_index + strike_range + 1)  # +1 to include bracket
            selected_strikes = strikes[start_index:end_index]

            # Filter the dataframe
            filtered_df = df[df['strike'].isin(selected_strikes)].copy()

            logger.info(f"OI PROCESSING: Filtered strikes for OI context - {len(strikes)} → {len(selected_strikes)} strikes (±{strike_range} from ATM at {underlying_price})")
            return filtered_df

        except Exception as e:
            logger.error(f"OI PROCESSING ERROR: Strike filtering failed: {e}")
            return df

    def _mark_oi_leaders_on_filtered_data(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Mark OI leaders on already ±20 filtered data within OI processing system.
        This method works on the filtered strikes only (same as scoring).

        OI MARKING SYSTEM - Integrated with OI processing workflow

        Marks highest and second highest OI for calls/puts and highest OI change
        for calls/puts within the ±20 strike range around ATM.

        Args:
            df: DataFrame with option chain data (already filtered to ±20 strikes)

        Returns:
            DataFrame with OI marking boolean columns populated

        Integration: Called from OI processing workflow after filtering is complete
        """
        if df.empty:
            return df

        try:
            # Initialize all 6 OI marking columns to False
            oi_marking_columns = [
                'is_highest_call_oi',
                'is_second_highest_call_oi',
                'is_highest_put_oi',
                'is_second_highest_put_oi',
                'is_highest_call_oi_change',
                'is_highest_put_oi_change'
            ]

            for col in oi_marking_columns:
                df[col] = False

            # Process each underlying_symbol + expiry combination in the filtered data
            for (underlying_symbol, expiry), group in df.groupby(['underlying_symbol', 'expiry']):

                # Process Call Options (CE) - already filtered to ±20 strikes
                calls = group[group['option_type'] == 'CE'].copy()
                if len(calls) >= 2:
                    # Mark highest and second highest OI for calls
                    top_oi_calls = calls.nlargest(2, 'oi')
                    df.loc[top_oi_calls.index[0], 'is_highest_call_oi'] = True
                    df.loc[top_oi_calls.index[1], 'is_second_highest_call_oi'] = True
                elif len(calls) == 1:
                    df.loc[calls.index[0], 'is_highest_call_oi'] = True

                if len(calls) >= 1:
                    # Mark highest OI change for calls
                    highest_oi_change_call = calls.nlargest(1, 'oi_change')
                    df.loc[highest_oi_change_call.index[0], 'is_highest_call_oi_change'] = True

                # Process Put Options (PE) - already filtered to ±20 strikes
                puts = group[group['option_type'] == 'PE'].copy()
                if len(puts) >= 2:
                    # Mark highest and second highest OI for puts
                    top_oi_puts = puts.nlargest(2, 'oi')
                    df.loc[top_oi_puts.index[0], 'is_highest_put_oi'] = True
                    df.loc[top_oi_puts.index[1], 'is_second_highest_put_oi'] = True
                elif len(puts) == 1:
                    df.loc[puts.index[0], 'is_highest_put_oi'] = True

                if len(puts) >= 1:
                    # Mark highest OI change for puts
                    highest_oi_change_put = puts.nlargest(1, 'oi_change')
                    df.loc[highest_oi_change_put.index[0], 'is_highest_put_oi_change'] = True

            logger.info(f"OI MARKING: Successfully marked OI leaders for {len(df)} filtered records")
            return df

        except Exception as e:
            logger.error(f"OI MARKING ERROR: Failed to mark OI leaders: {e}")
            # Return DataFrame with False values on error (already initialized)
            return df

    def _calculate_and_store_oi_ratios(self, df: pd.DataFrame) -> None:
        """
        Calculate OI ratios and store in oi_ratios table

        OI RATIO CALCULATION SYSTEM - Integration with OI processing workflow

        Extracts OI data from records marked with OI leaders and calculates ratios.
        Called after OI marking is complete within same transaction as data collection.

        Args:
            df: DataFrame with completed OI markings (already filtered to ±20 strikes)

        Returns:
            None - stores ratios directly in oi_ratios table

        Integration: Called from OI processing workflow after OI marking completion
        """
        if df.empty:
            logger.debug("OI RATIOS: Empty DataFrame passed - skipping ratio calculation")
            return

        try:
            logger.info(f"OI RATIOS: Calculating ratios for {len(df)} records")

            # Process each underlying_symbol + expiry combination
            ratio_records = []

            for (underlying_symbol, expiry), group in df.groupby(['underlying_symbol', 'expiry']):
                timestamp = group['timestamp'].iloc[0]  # All records have same timestamp

                # Extract Call OI data
                call_highest_record = group[group['is_highest_call_oi'] == True]
                call_second_highest_record = group[group['is_second_highest_call_oi'] == True]

                if len(call_highest_record) > 0:
                    call_highest_oi = int(call_highest_record['oi'].iloc[0])
                    call_highest_strike = float(call_highest_record['strike'].iloc[0])
                else:
                    call_highest_oi = 0
                    call_highest_strike = 0.0

                if len(call_second_highest_record) > 0:
                    call_second_highest_oi = int(call_second_highest_record['oi'].iloc[0])
                    call_second_highest_strike = float(call_second_highest_record['strike'].iloc[0])
                else:
                    call_second_highest_oi = 0
                    call_second_highest_strike = 0.0

                # Calculate call ratio
                call_ratio = (call_second_highest_oi / call_highest_oi) if call_highest_oi > 0 else 0.0

                # Extract Put OI data
                put_highest_record = group[group['is_highest_put_oi'] == True]
                put_second_highest_record = group[group['is_second_highest_put_oi'] == True]

                if len(put_highest_record) > 0:
                    put_highest_oi = int(put_highest_record['oi'].iloc[0])
                    put_highest_strike = float(put_highest_record['strike'].iloc[0])
                else:
                    put_highest_oi = 0
                    put_highest_strike = 0.0

                if len(put_second_highest_record) > 0:
                    put_second_highest_oi = int(put_second_highest_record['oi'].iloc[0])
                    put_second_highest_strike = float(put_second_highest_record['strike'].iloc[0])
                else:
                    put_second_highest_oi = 0
                    put_second_highest_strike = 0.0

                # Calculate put ratio
                put_ratio = (put_second_highest_oi / put_highest_oi) if put_highest_oi > 0 else 0.0

                # Create ratio record
                ratio_record = {
                    'timestamp': timestamp,
                    'underlying_symbol': underlying_symbol,
                    'expiry': expiry,
                    'call_highest_oi': call_highest_oi,
                    'call_second_highest_oi': call_second_highest_oi,
                    'call_ratio': call_ratio,
                    'put_highest_oi': put_highest_oi,
                    'put_second_highest_oi': put_second_highest_oi,
                    'put_ratio': put_ratio,
                    'call_highest_strike': call_highest_strike,
                    'call_second_highest_strike': call_second_highest_strike,
                    'put_highest_strike': put_highest_strike,
                    'put_second_highest_strike': put_second_highest_strike
                }

                ratio_records.append(ratio_record)

                logger.debug(f"OI RATIOS: {underlying_symbol} {expiry} - Call ratio: {call_ratio:.3f}, Put ratio: {put_ratio:.3f}")

            # Store ratio records in database (within same transaction as main data collection)
            if ratio_records:
                ratio_df = pd.DataFrame(ratio_records)

                # Get current connection from the calling context (same transaction)
                # Note: This method is called within the transaction in collect_asset()
                # We need to use the same connection - will be passed from calling method

                # For now, create a separate connection - but this should be optimized
                # to use the same transaction as the main data collection
                with self.get_db_connection() as conn:
                    try:
                        ratio_df.to_sql('oi_ratios', conn, if_exists='append', index=False)
                        logger.info(f"OI RATIOS: Successfully stored {len(ratio_records)} ratio records")
                    except sqlite3.IntegrityError as e:
                        logger.warning(f"OI RATIOS: Constraint violation during ratio insertion: {e}")

            else:
                logger.warning("OI RATIOS: No ratio records generated")

        except Exception as e:
            logger.error(f"OI RATIOS ERROR: Failed to calculate and store ratios: {e}")
            # Do not raise exception - ratio calculation failure should not prevent main data collection

    def _merge_oi_results_back_to_full_dataset(self, full_df: pd.DataFrame, oi_df: pd.DataFrame) -> pd.DataFrame:
        """
        Merge OI results calculated on filtered strikes back to the complete dataset.
        Strikes outside the OI processing range get default values, strikes within range get calculated values.

        OI PROCESSING SYSTEM - Result Integration

        Args:
            full_df: Complete DataFrame with all strikes
            oi_df: Filtered DataFrame with OI calculations completed

        Returns:
            Complete DataFrame with OI markings properly distributed
        """
        if full_df.empty:
            logger.debug("OI MERGE: Empty full DataFrame - returning unchanged")
            return full_df

        if oi_df.empty:
            logger.debug("OI MERGE: Empty OI DataFrame - setting all OI markings to False")
            # Initialize all OI marking columns to False for all records
            oi_marking_columns = [
                'is_highest_call_oi', 'is_second_highest_call_oi',
                'is_highest_put_oi', 'is_second_highest_put_oi',
                'is_highest_call_oi_change', 'is_highest_put_oi_change'
            ]
            for col in oi_marking_columns:
                full_df[col] = False
            return full_df

        try:
            # Columns to merge from oi_df to full_df
            oi_marking_columns = [
                'is_highest_call_oi', 'is_second_highest_call_oi',
                'is_highest_put_oi', 'is_second_highest_put_oi',
                'is_highest_call_oi_change', 'is_highest_put_oi_change'
            ]

            # Initialize all OI marking columns to False in full dataset
            for col in oi_marking_columns:
                full_df[col] = False

            # Merge columns for matching - ensure we have unique identifiers
            merge_cols = ['underlying_symbol', 'strike', 'option_type', 'expiry', 'timestamp']

            # Prepare oi_df results for merging
            oi_results = oi_df[merge_cols + oi_marking_columns].copy()

            # Merge OI markings from oi_df to full_df
            full_df = full_df.merge(oi_results, on=merge_cols, how='left', suffixes=('', '_oi'))

            # Update the original columns with OI results (True where calculated, False elsewhere)
            for col in oi_marking_columns:
                oi_col = f"{col}_oi"
                if oi_col in full_df.columns:
                    full_df[col] = full_df[oi_col].mask(full_df[oi_col].isna(), False).astype(bool)
                    full_df.drop(oi_col, axis=1, inplace=True)
                else:
                    full_df[col] = False

            logger.info(f"OI MERGE: Successfully merged OI markings from {len(oi_df)} filtered records to {len(full_df)} total records")
            return full_df

        except Exception as e:
            logger.error(f"OI MERGE ERROR: Failed to merge OI results: {e}")
            # Fallback: initialize all OI markings to False
            oi_marking_columns = [
                'is_highest_call_oi', 'is_second_highest_call_oi',
                'is_highest_put_oi', 'is_second_highest_put_oi',
                'is_highest_call_oi_change', 'is_highest_put_oi_change'
            ]
            for col in oi_marking_columns:
                full_df[col] = False
            return full_df

# ========================================
# DATABASE SELECTION ENHANCEMENT
# Auto-discovery and manual selection functionality
# EASILY REVERTIBLE: Remove this entire section to restore original behavior
# ========================================

def discover_databases(directory_path: str = None) -> List[Dict[str, Any]]:
    """
    Discover SQLite databases in specified directory or current working directory
    
    DATABASE SELECTION SYSTEM - Auto-discovery functionality
    
    Args:
        directory_path: Directory to scan (defaults to current working directory)
        
    Returns:
        List of database info dictionaries, with "Default" prioritized first
        
    REVERSION: Remove this function when removing database selection system
    """
    try:
        if directory_path is None:
            directory_path = os.getcwd()
            
        databases = []
        
        # Scan directory for SQLite files
        for filename in os.listdir(directory_path):
            filepath = os.path.join(directory_path, filename)
            
            # Skip directories and non-files
            if not os.path.isfile(filepath):
                continue
                
            # Check if it's a SQLite database
            try:
                conn = sqlite3.connect(f"file:{filepath}?mode=ro", uri=True, timeout=5.0)
                
                # Verify it has options_data table (our database structure)
                cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='options_data'")
                if not cursor.fetchone():
                    conn.close()
                    continue
                
                # Get database statistics
                stats_cursor = conn.execute("""
                    SELECT 
                        COUNT(*) as record_count,
                        MIN(timestamp) as earliest_date,
                        MAX(timestamp) as latest_date,
                        COUNT(DISTINCT underlying_symbol) as asset_count
                    FROM options_data
                """)
                
                stats = stats_cursor.fetchone()
                conn.close()
                
                # Get file size
                file_size = os.path.getsize(filepath)
                file_size_mb = file_size / (1024 * 1024)
                
                # Format database info
                db_info = {
                    'filename': filename,
                    'filepath': filepath,
                    'display_name': filename,
                    'record_count': stats[0] if stats else 0,
                    'earliest_date': stats[1] if stats and stats[1] else 'No data',
                    'latest_date': stats[2] if stats and stats[2] else 'No data', 
                    'asset_count': stats[3] if stats else 0,
                    'file_size_mb': round(file_size_mb, 1),
                    'is_default': filename.lower() == 'default' or 'default' in filename.lower()
                }
                
                databases.append(db_info)
                
            except (sqlite3.Error, OSError) as e:
                # Skip invalid or inaccessible files
                logger.debug(f"Skipping file {filename}: {e}")
                continue
        
        # Sort databases: "Default" first, then by latest_date descending
        databases.sort(key=lambda x: (not x['is_default'], x['latest_date']), reverse=True)
        
        logger.info(f"DATABASE SELECTION: Discovered {len(databases)} valid databases in {directory_path}")
        return databases
        
    except Exception as e:
        logger.error(f"DATABASE SELECTION ERROR: Failed to discover databases: {e}")
        return []

def validate_database_file(filepath: str) -> Dict[str, Any]:
    """
    Validate a database file and extract metadata
    
    DATABASE SELECTION SYSTEM - File validation
    
    Args:
        filepath: Path to database file to validate
        
    Returns:
        Dictionary with validation result and metadata
        
    REVERSION: Remove this function when removing database selection system
    """
    try:
        if not os.path.isfile(filepath):
            return {'valid': False, 'error': 'File does not exist'}
        
        # Try to connect and validate structure
        conn = sqlite3.connect(f"file:{filepath}?mode=ro", uri=True, timeout=5.0)
        
        # Check for required tables
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('options_data', 'asset_metadata')")
        tables = [row[0] for row in cursor.fetchall()]
        
        if 'options_data' not in tables:
            conn.close()
            return {'valid': False, 'error': 'Missing options_data table'}
        
        # Get database statistics
        stats_cursor = conn.execute("""
            SELECT 
                COUNT(*) as record_count,
                MIN(timestamp) as earliest_date,
                MAX(timestamp) as latest_date,
                COUNT(DISTINCT underlying_symbol) as asset_count
            FROM options_data
        """)
        
        stats = stats_cursor.fetchone()
        conn.close()
        
        # Get file info
        file_size = os.path.getsize(filepath)
        file_size_mb = file_size / (1024 * 1024)
        filename = os.path.basename(filepath)
        
        return {
            'valid': True,
            'filename': filename,
            'filepath': filepath,
            'display_name': filename,
            'record_count': stats[0] if stats else 0,
            'earliest_date': stats[1] if stats and stats[1] else 'No data',
            'latest_date': stats[2] if stats and stats[2] else 'No data',
            'asset_count': stats[3] if stats else 0,
            'file_size_mb': round(file_size_mb, 1),
            'is_default': filename.lower() == 'default' or 'default' in filename.lower()
        }
        
    except Exception as e:
        return {'valid': False, 'error': str(e)}

def get_database_connection_by_path(db_path: str):
    """
    Get database connection context manager for specified path
    
    DATABASE SELECTION SYSTEM - Dynamic connection helper
    
    Args:
        db_path: Path to database file
        
    Returns:
        Context manager for database connection
        
    REVERSION: Remove this function when removing database selection system
    """
    @contextmanager
    def get_connection():
        conn = None
        try:
            db_timeout = float(os.getenv('DB_TIMEOUT', '30.0'))
            db_cache_size = int(os.getenv('DB_CACHE_SIZE', '-64000'))
            
            # Open in read-only mode to prevent corruption risk
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", timeout=db_timeout, uri=True)
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")  
            conn.execute(f"PRAGMA cache_size = {db_cache_size}")
            yield conn
        except sqlite3.Error as e:
            logger.error(f"Database connection error for {db_path}: {e}")
            raise
        finally:
            if conn:
                conn.close()
    
    return get_connection


def create_provider(client_id: str = None, access_token: str = None, db_path: str = None) -> PersonalDhanClient:
    """Create enhanced Dhan provider with clean architecture
    
    Args:
        client_id: Dhan client ID (optional if set in environment)
        access_token: Dhan access token (optional if set in environment)
        db_path: Database path (optional if set in environment)
    """
    return PersonalDhanClient(client_id, access_token, db_path)


if __name__ == "__main__":
    print("Enhanced Dhan Provider - Clean Architecture")
    print("=" * 50)
    print("Features:")
    print("- Environment variable configuration support")
    print("- Standardized database schema matching output format")
    print("- Direct DataFrame storage with zero transformations")
    print("- Efficient single-query operations")
    print("- Asset management with historical preservation")
    print("- Collection status tracking")
    print("- Adaptive rate limiting and robust error handling")
    print("- Optimized indexes for fast queries")
    print()
    print("Usage:")
    print("  # With environment variables:")
    print("  provider = create_provider()")
    print("  # Or with explicit credentials:")
    print("  provider = create_provider(client_id, access_token)")
    print("  provider.load_assets('assets/assets.csv')")
    print("  provider.collect_asset(asset_id, segment_code)")
    print("  data = provider.get_latest_data(underlying_symbol)")
    print("  stats = provider.get_stats()")
