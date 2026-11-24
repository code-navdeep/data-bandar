"""
Database Archive Manager
Complete data extraction and archival system for options database
"""

import sqlite3
import pandas as pd
import logging
import os
from datetime import datetime
from contextlib import contextmanager
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)

@dataclass
class ArchiveStats:
    """Statistics for archive operation"""
    options_data_records: int = 0
    asset_metadata_records: int = 0
    collection_status_records: int = 0
    expiry_cache_records: int = 0
    total_records: int = 0
    archive_size_mb: float = 0.0
    creation_time: str = ""
    selection_criteria: Dict = None

class DatabaseArchiver:
    """Complete database archival system with selection criteria"""
    
    def __init__(self, source_db_path: str):
        self.source_db_path = source_db_path
        
        # Check if source database exists
        if not os.path.exists(source_db_path):
            raise FileNotFoundError(f"Source database not found at {source_db_path}")
        
        # Load asset name mappings from database
        self.asset_name_mapping = self._load_asset_name_mapping()
        
        logger.info(f"Database archiver initialized with source: {source_db_path}")
    
    def _load_asset_name_mapping(self) -> Dict[str, str]:
        """Load mapping from underlying_symbol to human-readable asset names"""
        mapping = {}
        try:
            with self.get_source_connection() as conn:
                # Get asset metadata to create mapping
                cursor = conn.execute("""
                    SELECT asset_id, asset_name, segment_code 
                    FROM asset_metadata
                """)
                
                for row in cursor.fetchall():
                    asset_id, asset_name, segment_code = row
                    # Create the underlying_symbol format used in options_data
                    underlying_symbol = f"asset_{asset_id}_{segment_code}"
                    mapping[underlying_symbol] = asset_name
                    
                logger.info(f"Loaded {len(mapping)} asset name mappings")
                
        except Exception as e:
            logger.error(f"Error loading asset name mapping: {e}")
            
        return mapping
    
    def get_display_name(self, underlying_symbol: str) -> str:
        """Convert underlying_symbol to display name"""
        return self.asset_name_mapping.get(underlying_symbol, underlying_symbol)
    
    @contextmanager
    def get_source_connection(self):
        """Get read-only connection to source database"""
        conn = None
        try:
            # Open in read-only mode to prevent any corruption risk
            conn = sqlite3.connect(f"file:{self.source_db_path}?mode=ro", 
                                 timeout=30.0, uri=True)
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.execute("PRAGMA cache_size = -64000")
            yield conn
        except sqlite3.Error as e:
            logger.error(f"Source database error: {e}")
            raise
        finally:
            if conn:
                conn.close()
    
    @contextmanager
    def get_archive_connection(self, archive_path: str):
        """Get connection to archive database"""
        conn = None
        try:
            conn = sqlite3.connect(archive_path, timeout=30.0)
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.execute("PRAGMA cache_size = -64000")
            yield conn
        except sqlite3.Error as e:
            logger.error(f"Archive database error: {e}")
            if conn:
                conn.rollback()
            raise
        finally:
            if conn:
                conn.close()
    
    def create_archive_schema(self, archive_path: str):
        """Create identical schema in archive database"""
        with self.get_archive_connection(archive_path) as archive_conn:
            # Get schema from source database
            with self.get_source_connection() as source_conn:
                # Get all table creation statements
                cursor = source_conn.execute("""
                    SELECT sql FROM sqlite_master 
                    WHERE type='table' AND name NOT LIKE 'sqlite_%'
                    ORDER BY name
                """)
                
                table_schemas = cursor.fetchall()
                
                # Get all index creation statements
                cursor = source_conn.execute("""
                    SELECT sql FROM sqlite_master 
                    WHERE type='index' AND name NOT LIKE 'sqlite_%'
                    ORDER BY name
                """)
                
                index_schemas = cursor.fetchall()
            
            # Create tables in archive
            for schema_row in table_schemas:
                if schema_row[0]:  # Skip None entries
                    archive_conn.execute(schema_row[0])
                    logger.debug(f"Created table: {schema_row[0][:50]}...")
            
            # Create indexes in archive
            for schema_row in index_schemas:
                if schema_row[0]:  # Skip None entries
                    archive_conn.execute(schema_row[0])
                    logger.debug(f"Created index: {schema_row[0][:50]}...")
            
            archive_conn.commit()
            logger.info("Archive database schema created successfully")
    
    def get_available_assets(self) -> List[Dict[str, str]]:
        """Get list of available assets with display names from source database"""
        try:
            with self.get_source_connection() as conn:
                cursor = conn.execute("""
                    SELECT DISTINCT underlying_symbol 
                    FROM options_data 
                    ORDER BY underlying_symbol
                """)
                
                assets = []
                for row in cursor.fetchall():
                    underlying_symbol = row[0]
                    display_name = self.get_display_name(underlying_symbol)
                    assets.append({
                        'underlying_symbol': underlying_symbol,
                        'display_name': display_name
                    })
                
                logger.info(f"Found {len(assets)} available assets")
                return assets
                
        except Exception as e:
            logger.error(f"Error getting available assets: {e}")
            return []
    
    def get_underlying_symbol_from_display_name(self, display_name: str) -> Optional[str]:
        """Convert display name back to underlying_symbol"""
        for underlying_symbol, name in self.asset_name_mapping.items():
            if name == display_name:
                return underlying_symbol
        return None
    
    def get_date_range(self) -> Tuple[Optional[str], Optional[str]]:
        """Get the date range available in source database"""
        try:
            with self.get_source_connection() as conn:
                cursor = conn.execute("""
                    SELECT MIN(DATE(timestamp)) as earliest, MAX(DATE(timestamp)) as latest
                    FROM options_data
                """)
                
                row = cursor.fetchone()
                if row:
                    return row[0], row[1]
                return None, None
                
        except Exception as e:
            logger.error(f"Error getting date range: {e}")
            return None, None
    
    def build_selection_query(self, assets: Optional[List[str]] = None, 
                            start_date: Optional[str] = None, 
                            end_date: Optional[str] = None) -> Tuple[str, List[str]]:
        """Build SQL query and parameters for options_data selection"""
        base_query = "SELECT * FROM options_data"
        conditions = []
        params = []
        
        # Asset filter
        if assets and len(assets) > 0:
            placeholders = ','.join(['?' for _ in assets])
            conditions.append(f"underlying_symbol IN ({placeholders})")
            params.extend(assets)
        
        # Date filters
        if start_date:
            conditions.append("DATE(timestamp) >= ?")
            params.append(start_date)
        
        if end_date:
            conditions.append("DATE(timestamp) <= ?")
            params.append(end_date)
        
        if conditions:
            base_query += " WHERE " + " AND ".join(conditions)
        
        base_query += " ORDER BY timestamp, underlying_symbol, strike, option_type"
        
        return base_query, params
    
    def extract_options_data(self, archive_path: str, assets: Optional[List[str]] = None,
                           start_date: Optional[str] = None, end_date: Optional[str] = None,
                           progress_callback: Optional[callable] = None) -> int:
        """Extract options_data records matching selection criteria"""
        query, params = self.build_selection_query(assets, start_date, end_date)
        
        with self.get_source_connection() as source_conn:
            with self.get_archive_connection(archive_path) as archive_conn:
                # Get total count for progress tracking
                count_query = query.replace("SELECT *", "SELECT COUNT(*)")
                cursor = source_conn.execute(count_query, params)
                total_records = cursor.fetchone()[0]
                
                if total_records == 0:
                    logger.warning("No options_data records match selection criteria")
                    return 0
                
                logger.info(f"Extracting {total_records} options_data records")
                
                # Extract in batches for better performance and progress tracking
                batch_size = 10000
                records_processed = 0
                
                # Use LIMIT and OFFSET for batching
                for offset in range(0, total_records, batch_size):
                    batch_query = f"{query} LIMIT {batch_size} OFFSET {offset}"
                    
                    # Read batch from source
                    df_batch = pd.read_sql_query(batch_query, source_conn, params=params)
                    
                    # Write batch to archive
                    df_batch.to_sql('options_data', archive_conn, if_exists='append', index=False)
                    
                    records_processed += len(df_batch)
                    
                    # Update progress
                    if progress_callback:
                        progress_callback(f"Options Data: {records_processed}/{total_records} records", 
                                        int((records_processed / total_records) * 100))
                    
                    logger.debug(f"Processed {records_processed}/{total_records} options_data records")
                
                archive_conn.commit()
                logger.info(f"Successfully extracted {records_processed} options_data records")
                return records_processed
    
    def extract_related_data(self, archive_path: str, progress_callback: Optional[callable] = None) -> Dict[str, int]:
        """Extract all related data based on options_data already in archive"""
        stats = {}
        
        with self.get_archive_connection(archive_path) as archive_conn:
            with self.get_source_connection() as source_conn:
                
                # Get unique asset_ids and underlying_symbols from archived options_data
                cursor = archive_conn.execute("""
                    SELECT DISTINCT underlying_symbol FROM options_data
                """)
                underlying_symbols = [row[0] for row in cursor.fetchall()]
                
                if not underlying_symbols:
                    logger.warning("No underlying symbols found in archived options_data")
                    return {'asset_metadata': 0, 'collection_status': 0, 'expiry_cache': 0}
                
                # Extract asset_metadata
                if progress_callback:
                    progress_callback("Extracting Asset Metadata...", 70)
                
                symbol_placeholders = ','.join(['?' for _ in underlying_symbols])
                
                # Get asset_ids from underlying_symbols (extract from symbol format)
                asset_ids = []
                for symbol in underlying_symbols:
                    # Extract asset_id from format like "asset_12345_NSE_EQ"
                    parts = symbol.split('_')
                    if len(parts) >= 2 and parts[0] == 'asset':
                        try:
                            asset_ids.append(int(parts[1]))
                        except ValueError:
                            continue
                
                if asset_ids:
                    asset_id_placeholders = ','.join(['?' for _ in asset_ids])
                    asset_metadata_query = f"SELECT * FROM asset_metadata WHERE asset_id IN ({asset_id_placeholders})"
                    df_metadata = pd.read_sql_query(asset_metadata_query, source_conn, params=asset_ids)
                    
                    if not df_metadata.empty:
                        df_metadata.to_sql('asset_metadata', archive_conn, if_exists='append', index=False)
                        stats['asset_metadata'] = len(df_metadata)
                        logger.info(f"Extracted {len(df_metadata)} asset_metadata records")
                    else:
                        stats['asset_metadata'] = 0
                else:
                    stats['asset_metadata'] = 0
                
                # Extract collection_status for relevant assets and dates
                if progress_callback:
                    progress_callback("Extracting Collection Status...", 80)
                
                # Get date range from archived options_data
                cursor = archive_conn.execute("SELECT MIN(DATE(timestamp)), MAX(DATE(timestamp)) FROM options_data")
                date_range = cursor.fetchone()
                
                if date_range and date_range[0] and asset_ids:
                    collection_conditions = []
                    collection_params = []
                    
                    # Filter by asset_ids
                    collection_conditions.append(f"asset_id IN ({asset_id_placeholders})")
                    collection_params.extend(asset_ids)
                    
                    # Filter by date range
                    collection_conditions.append("DATE(timestamp) >= ? AND DATE(timestamp) <= ?")
                    collection_params.extend([date_range[0], date_range[1]])
                    
                    collection_query = f"SELECT * FROM collection_status WHERE {' AND '.join(collection_conditions)}"
                    df_collection = pd.read_sql_query(collection_query, source_conn, params=collection_params)
                    
                    if not df_collection.empty:
                        df_collection.to_sql('collection_status', archive_conn, if_exists='append', index=False)
                        stats['collection_status'] = len(df_collection)
                        logger.info(f"Extracted {len(df_collection)} collection_status records")
                    else:
                        stats['collection_status'] = 0
                else:
                    stats['collection_status'] = 0
                
                # Extract expiry_cache for relevant assets
                if progress_callback:
                    progress_callback("Extracting Expiry Cache...", 90)
                
                if asset_ids:
                    expiry_cache_query = f"SELECT * FROM expiry_cache WHERE asset_id IN ({asset_id_placeholders})"
                    df_expiry = pd.read_sql_query(expiry_cache_query, source_conn, params=asset_ids)
                    
                    if not df_expiry.empty:
                        df_expiry.to_sql('expiry_cache', archive_conn, if_exists='append', index=False)
                        stats['expiry_cache'] = len(df_expiry)
                        logger.info(f"Extracted {len(df_expiry)} expiry_cache records")
                    else:
                        stats['expiry_cache'] = 0
                else:
                    stats['expiry_cache'] = 0
                
                archive_conn.commit()
        
        return stats
    
    def create_archive(self, archive_path: str, assets: Optional[List[str]] = None,
                      start_date: Optional[str] = None, end_date: Optional[str] = None,
                      progress_callback: Optional[callable] = None) -> ArchiveStats:
        """Create complete archive with selection criteria"""
        try:
            # Validate inputs
            if start_date and end_date and start_date > end_date:
                raise ValueError("Start date cannot be after end date")
            
            # Remove existing archive file if it exists
            if os.path.exists(archive_path):
                os.remove(archive_path)
                logger.info(f"Removed existing archive file: {archive_path}")
            
            # Create directory if it doesn't exist (only if there's a directory part)
            archive_dir = os.path.dirname(archive_path)
            if archive_dir:
                os.makedirs(archive_dir, exist_ok=True)
            
            # Initialize progress
            if progress_callback:
                progress_callback("Creating archive schema...", 0)
            
            # Create archive database with identical schema
            self.create_archive_schema(archive_path)
            
            # Extract options_data (primary filter)
            if progress_callback:
                progress_callback("Extracting options data...", 10)
            
            options_records = self.extract_options_data(
                archive_path, assets, start_date, end_date, progress_callback
            )
            
            if options_records == 0:
                raise ValueError("No data matches the selection criteria")
            
            # Extract all related data
            related_stats = self.extract_related_data(archive_path, progress_callback)
            
            # Calculate archive size
            if progress_callback:
                progress_callback("Finalizing archive...", 95)
            
            archive_size_bytes = os.path.getsize(archive_path)
            archive_size_mb = archive_size_bytes / (1024 * 1024)
            
            # Create archive statistics
            archive_stats = ArchiveStats(
                options_data_records=options_records,
                asset_metadata_records=related_stats.get('asset_metadata', 0),
                collection_status_records=related_stats.get('collection_status', 0),
                expiry_cache_records=related_stats.get('expiry_cache', 0),
                total_records=(options_records + sum(related_stats.values())),
                archive_size_mb=archive_size_mb,
                creation_time=datetime.now().isoformat(),
                selection_criteria={
                    'assets': assets,
                    'start_date': start_date,
                    'end_date': end_date,
                    'source_database': self.source_db_path,
                    'archive_path': archive_path
                }
            )
            
            if progress_callback:
                progress_callback("Archive created successfully!", 100)
            
            logger.info(f"Archive created successfully: {archive_path}")
            logger.info(f"Archive stats: {archive_stats.total_records} total records, {archive_size_mb:.2f} MB")
            
            return archive_stats
            
        except Exception as e:
            # Cleanup failed archive
            if os.path.exists(archive_path):
                try:
                    os.remove(archive_path)
                    logger.info("Cleaned up failed archive file")
                except:
                    pass
            
            logger.error(f"Archive creation failed: {e}")
            raise
    
    def generate_default_filename(self, assets: Optional[List[str]] = None,
                                 start_date: Optional[str] = None, 
                                 end_date: Optional[str] = None) -> str:
        """Generate default archive filename based on selection criteria"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # Asset part - use display names for better readability
        if assets and len(assets) > 0:
            if len(assets) <= 3:
                # Convert underlying symbols to display names for filename
                display_names = []
                for asset in assets[:3]:
                    display_name = self.get_display_name(asset)
                    # Clean display name for filename (remove special characters)
                    clean_name = "".join(c for c in display_name if c.isalnum())
                    display_names.append(clean_name)
                asset_part = "+".join(display_names)
            else:
                asset_part = f"{len(assets)}assets"
        else:
            asset_part = "allassets"
        
        # Date part
        if start_date and end_date:
            if start_date == end_date:
                date_part = start_date.replace("-", "")
            else:
                date_part = f"{start_date.replace('-', '')}-{end_date.replace('-', '')}"
        elif start_date:
            date_part = f"from{start_date.replace('-', '')}"
        elif end_date:
            date_part = f"until{end_date.replace('-', '')}"
        else:
            date_part = "alldates"
        
        return f"archive_{asset_part}_{date_part}_{timestamp}.db"


def create_archiver(source_db_path: str) -> DatabaseArchiver:
    """Factory function to create database archiver"""
    return DatabaseArchiver(source_db_path)

# ================================
# OHLC ARCHIVE SUPPORT (ADDITIVE)
# ================================

@dataclass
class OHLCArchiveStats:
    """Statistics for OHLC archive operation"""
    ohlc_data_records: int = 0
    fetch_metadata_records: int = 0
    total_records: int = 0
    archive_size_mb: float = 0.0
    creation_time: str = ""
    selection_criteria: Dict = None

class OHLCDatabaseArchiver:
    """OHLC database archival system with date-based selection"""

    def __init__(self, source_db_path: str):
        self.source_db_path = source_db_path

        # Check if source database exists
        if not os.path.exists(source_db_path):
            raise FileNotFoundError(f"Source OHLC database not found at {source_db_path}")

        # Load asset mappings from OHLC database
        self.asset_name_mapping = self._load_ohlc_asset_mapping()

        logger.info(f"OHLC database archiver initialized with source: {source_db_path}")

    def _load_ohlc_asset_mapping(self) -> Dict[int, str]:
        """Load mapping from asset_id to asset names from OHLC database"""
        mapping = {}
        try:
            with self.get_source_connection() as conn:
                cursor = conn.execute("""
                    SELECT DISTINCT asset_id, symbol
                    FROM ohlc_data
                    ORDER BY asset_id
                """)

                for row in cursor.fetchall():
                    asset_id, symbol = row
                    mapping[asset_id] = symbol

                logger.info(f"Loaded {len(mapping)} OHLC asset mappings")

        except Exception as e:
            logger.error(f"Error loading OHLC asset mapping: {e}")

        return mapping

    def get_display_name(self, asset_id: int) -> str:
        """Convert asset_id to display name"""
        return self.asset_name_mapping.get(asset_id, f"Asset_{asset_id}")

    @contextmanager
    def get_source_connection(self):
        """Get read-only connection to source OHLC database"""
        conn = None
        try:
            conn = sqlite3.connect(f"file:{self.source_db_path}?mode=ro",
                                 timeout=30.0, uri=True)
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.execute("PRAGMA cache_size = -64000")
            yield conn
        except sqlite3.Error as e:
            logger.error(f"Source OHLC database error: {e}")
            raise
        finally:
            if conn:
                conn.close()

    @contextmanager
    def get_archive_connection(self, archive_path: str):
        """Get connection to OHLC archive database"""
        conn = None
        try:
            conn = sqlite3.connect(archive_path, timeout=30.0)
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.execute("PRAGMA cache_size = -64000")
            conn.execute("PRAGMA foreign_keys = ON")
            yield conn
        except sqlite3.Error as e:
            logger.error(f"Archive OHLC database error: {e}")
            raise
        finally:
            if conn:
                conn.close()

    def create_ohlc_archive_schema(self, archive_path: str):
        """Create OHLC schema in archive database"""
        with self.get_archive_connection(archive_path) as archive_conn:
            # Get schema from source OHLC database
            with self.get_source_connection() as source_conn:
                # Get all table creation statements
                cursor = source_conn.execute("""
                    SELECT sql FROM sqlite_master
                    WHERE type='table' AND name NOT LIKE 'sqlite_%'
                    ORDER BY name
                """)

                table_schemas = cursor.fetchall()

                # Get all index creation statements
                cursor = source_conn.execute("""
                    SELECT sql FROM sqlite_master
                    WHERE type='index' AND name NOT LIKE 'sqlite_%'
                    ORDER BY name
                """)

                index_schemas = cursor.fetchall()

            # Create tables in archive
            for schema_row in table_schemas:
                if schema_row[0]:
                    archive_conn.execute(schema_row[0])
                    logger.debug(f"Created OHLC table: {schema_row[0][:50]}...")

            # Create indexes in archive
            for schema_row in index_schemas:
                if schema_row[0]:
                    archive_conn.execute(schema_row[0])
                    logger.debug(f"Created OHLC index: {schema_row[0][:50]}...")

            archive_conn.commit()
            logger.info("OHLC archive database schema created successfully")

    def get_available_assets(self) -> List[Dict[str, Any]]:
        """Get list of available assets from OHLC database"""
        try:
            with self.get_source_connection() as conn:
                cursor = conn.execute("""
                    SELECT DISTINCT asset_id, symbol, segment_code
                    FROM ohlc_data
                    ORDER BY symbol
                """)

                assets = []
                for row in cursor.fetchall():
                    asset_id, symbol, segment_code = row
                    assets.append({
                        'asset_id': asset_id,
                        'symbol': symbol,
                        'segment_code': segment_code,
                        'display_name': symbol
                    })

                logger.info(f"Found {len(assets)} available OHLC assets")
                return assets

        except Exception as e:
            logger.error(f"Error getting available OHLC assets: {e}")
            return []

    def get_date_range(self) -> Tuple[Optional[str], Optional[str]]:
        """Get the date range of available OHLC data"""
        try:
            with self.get_source_connection() as conn:
                cursor = conn.execute("""
                    SELECT MIN(DATE(timestamp)), MAX(DATE(timestamp))
                    FROM ohlc_data
                """)

                result = cursor.fetchone()
                return result if result else (None, None)

        except Exception as e:
            logger.error(f"Error getting OHLC date range: {e}")
            return (None, None)

    def create_ohlc_archive(self, archive_path: str,
                           start_date: Optional[str] = None,
                           end_date: Optional[str] = None,
                           assets: Optional[List[int]] = None,
                           progress_callback=None) -> OHLCArchiveStats:
        """Create OHLC archive with date and asset filtering"""

        stats = OHLCArchiveStats()
        stats.creation_time = datetime.now().isoformat()
        stats.selection_criteria = {
            'start_date': start_date,
            'end_date': end_date,
            'assets': assets
        }

        try:
            # Create archive database and schema
            self.create_ohlc_archive_schema(archive_path)

            if progress_callback:
                progress_callback("Copying OHLC data...", 10)

            # Copy OHLC data
            stats.ohlc_data_records = self._copy_ohlc_data(
                archive_path, start_date, end_date, assets, progress_callback
            )

            if progress_callback:
                progress_callback("Copying fetch metadata...", 70)

            # Copy fetch metadata
            stats.fetch_metadata_records = self._copy_fetch_metadata(
                archive_path, start_date, end_date, progress_callback
            )

            if progress_callback:
                progress_callback("Finalizing archive...", 90)

            # Calculate totals
            stats.total_records = stats.ohlc_data_records + stats.fetch_metadata_records

            # Get archive file size
            if os.path.exists(archive_path):
                stats.archive_size_mb = os.path.getsize(archive_path) / (1024 * 1024)

            if progress_callback:
                progress_callback("Archive creation completed", 100)

            logger.info(f"OHLC archive created: {stats.total_records} total records")

        except Exception as e:
            logger.error(f"Error creating OHLC archive: {e}")
            # Clean up partial archive on error
            if os.path.exists(archive_path):
                try:
                    os.remove(archive_path)
                except:
                    pass
            raise

        return stats

    def _copy_ohlc_data(self, archive_path: str, start_date: Optional[str],
                       end_date: Optional[str], assets: Optional[List[int]],
                       progress_callback=None) -> int:
        """Copy OHLC data records to archive"""

        # Build WHERE clause
        where_conditions = []
        params = []

        if start_date:
            where_conditions.append("DATE(timestamp) >= ?")
            params.append(start_date)

        if end_date:
            where_conditions.append("DATE(timestamp) <= ?")
            params.append(end_date)

        if assets:
            placeholders = ','.join('?' * len(assets))
            where_conditions.append(f"asset_id IN ({placeholders})")
            params.extend(assets)

        where_clause = "WHERE " + " AND ".join(where_conditions) if where_conditions else ""

        with self.get_source_connection() as source_conn:
            with self.get_archive_connection(archive_path) as archive_conn:

                # Copy OHLC data
                query = f"""
                    SELECT * FROM ohlc_data
                    {where_clause}
                    ORDER BY timestamp, asset_id
                """

                cursor = source_conn.execute(query, params)

                # Get column names
                columns = [description[0] for description in cursor.description]
                placeholders = ','.join('?' * len(columns))

                insert_query = f"INSERT INTO ohlc_data ({','.join(columns)}) VALUES ({placeholders})"

                records_copied = 0
                batch_size = 1000
                batch = []

                for row in cursor:
                    batch.append(row)

                    if len(batch) >= batch_size:
                        archive_conn.executemany(insert_query, batch)
                        records_copied += len(batch)
                        batch = []

                        if progress_callback and records_copied % 5000 == 0:
                            progress = min(10 + (records_copied // 1000), 60)
                            progress_callback(f"Copied {records_copied} OHLC records...", progress)

                # Copy remaining records
                if batch:
                    archive_conn.executemany(insert_query, batch)
                    records_copied += len(batch)

                archive_conn.commit()
                logger.info(f"Copied {records_copied} OHLC data records")

                return records_copied

    def _copy_fetch_metadata(self, archive_path: str, start_date: Optional[str],
                            end_date: Optional[str], progress_callback=None) -> int:
        """Copy fetch metadata records to archive"""

        # Build WHERE clause for metadata
        where_conditions = []
        params = []

        if start_date:
            where_conditions.append("DATE(timestamp) >= ?")
            params.append(start_date)

        if end_date:
            where_conditions.append("DATE(timestamp) <= ?")
            params.append(end_date)

        where_clause = "WHERE " + " AND ".join(where_conditions) if where_conditions else ""

        with self.get_source_connection() as source_conn:
            with self.get_archive_connection(archive_path) as archive_conn:

                query = f"""
                    SELECT * FROM fetch_metadata
                    {where_clause}
                    ORDER BY timestamp
                """

                cursor = source_conn.execute(query, params)

                # Get column names
                columns = [description[0] for description in cursor.description]
                placeholders = ','.join('?' * len(columns))

                insert_query = f"INSERT INTO fetch_metadata ({','.join(columns)}) VALUES ({placeholders})"

                records = cursor.fetchall()
                if records:
                    archive_conn.executemany(insert_query, records)
                    archive_conn.commit()

                logger.info(f"Copied {len(records)} fetch metadata records")

                return len(records)

    def generate_ohlc_archive_filename(self, start_date: Optional[str] = None,
                                      end_date: Optional[str] = None) -> str:
        """Generate OHLC archive filename: ohlc_startdate_enddate.db"""

        if start_date and end_date:
            # Format: ohlc_2024-01-01_2024-01-31.db
            return f"ohlc_{start_date}_{end_date}.db"
        elif start_date:
            # Only start date: ohlc_2024-01-01_latest.db
            return f"ohlc_{start_date}_latest.db"
        elif end_date:
            # Only end date: ohlc_earliest_{end_date}.db
            return f"ohlc_earliest_{end_date}.db"
        else:
            # No date range: ohlc_all_data_{timestamp}.db
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            return f"ohlc_all_data_{timestamp}.db"

def create_ohlc_archiver(source_db_path: str) -> OHLCDatabaseArchiver:
    """Factory function to create OHLC database archiver"""
    return OHLCDatabaseArchiver(source_db_path)