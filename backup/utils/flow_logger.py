"""
TITAN Data Flow Logger
Provides consistent logging format for tracing data through the pipeline

Stages:
1. SCRAPER - DOM extraction from Micro999
2. PARSER - Parsing and Redis updates
3. CORTEX - Signal processing
4. BACKEND - API responses
5. FRONTEND - Display updates
"""

from datetime import datetime
from typing import Any, Dict, List, Optional
from loguru import logger
import json


class FlowLogger:
    """Consistent logging for data flow verification"""
    
    # Stage definitions
    STAGE_SCRAPER = 1
    STAGE_PARSER = 2
    STAGE_CORTEX = 3
    STAGE_BACKEND = 4
    STAGE_FRONTEND = 5
    
    STAGE_NAMES = {
        1: "SCRAPER",
        2: "PARSER",
        3: "CORTEX",
        4: "BACKEND",
        5: "FRONTEND"
    }
    
    # Color codes for terminal output
    STAGE_COLORS = {
        1: "\033[94m",   # Blue
        2: "\033[92m",   # Green
        3: "\033[95m",   # Magenta
        4: "\033[93m",   # Yellow
        5: "\033[96m",   # Cyan
    }
    RESET = "\033[0m"
    
    def __init__(self, component: str):
        """Initialize logger for a specific component"""
        self.component = component
        self._message_count = 0
    
    def _format_timestamp(self) -> str:
        """Get ISO 8601 timestamp with milliseconds"""
        return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    
    def _truncate(self, value: Any, max_length: int = 50) -> str:
        """Truncate long values for logging"""
        str_val = str(value)
        if len(str_val) > max_length:
            return str_val[:max_length] + "..."
        return str_val
    
    def _format_odds(self, odds: Dict) -> str:
        """Format odds dict for display"""
        if not odds:
            return "N/A"
        parts = []
        for team, price in odds.items():
            parts.append(f"{team[:10]}:{price}")
        return ", ".join(parts[:3])
    
    def _format_matches(self, matches: List[Dict]) -> str:
        """Format match list summary"""
        if not matches:
            return "[]"
        names = [m.get('match_name', m.get('match_id', '?'))[:20] for m in matches[:3]]
        suffix = f"...+{len(matches)-3}" if len(matches) > 3 else ""
        return f"[{', '.join(names)}{suffix}]"
    
    def log(self, stage: int, message: str, data: Optional[Dict] = None, level: str = "info"):
        """
        Log a data flow message with consistent format
        
        Args:
            stage: Stage number (1-5)
            message: Log message
            data: Optional data dict to include
            level: Log level (debug, info, warning, error)
        """
        self._message_count += 1
        timestamp = self._format_timestamp()
        stage_name = self.STAGE_NAMES.get(stage, f"STAGE-{stage}")
        color = self.STAGE_COLORS.get(stage, "")
        
        # Format the log prefix
        prefix = f"{color}[STAGE-{stage}][{stage_name}][{self.component}]{self.RESET}"
        
        # Build full message
        full_message = f"{prefix} {message}"
        
        # Add data summary if provided
        if data:
            data_summary = self._summarize_data(data)
            if data_summary:
                full_message += f" | {data_summary}"
        
        # Log at appropriate level
        if level == "debug":
            logger.debug(full_message)
        elif level == "warning":
            logger.warning(full_message)
        elif level == "error":
            logger.error(full_message)
        else:
            logger.info(full_message)
    
    def _summarize_data(self, data: Dict) -> str:
        """Create a concise summary of data dict"""
        parts = []
        
        # Common fields to extract
        if 'count' in data:
            parts.append(f"count={data['count']}")
        if 'match_id' in data:
            parts.append(f"match={self._truncate(data['match_id'], 30)}")
        if 'match_count' in data:
            parts.append(f"matches={data['match_count']}")
        if 'odds' in data:
            parts.append(f"odds={data['odds']}")
        if 'back_price' in data:
            parts.append(f"back={data['back_price']}")
        if 'signal_count' in data:
            parts.append(f"signals={data['signal_count']}")
        if 'latency_ms' in data:
            parts.append(f"latency={data['latency_ms']}ms")
        if 'live_matches' in data:
            parts.append(f"live={data['live_matches']}")
        
        return ", ".join(parts)
    
    # Convenience methods for each stage
    def stage1_extracted(self, odds_count: int, live_count: int, matches: List[str] = None):
        """Log Stage 1: DOM extraction completed"""
        self.log(
            self.STAGE_SCRAPER,
            f"Extracted {odds_count} odds from {live_count} live matches",
            {"count": odds_count, "live_matches": live_count}
        )
        if matches:
            logger.debug(f"  Matches: {matches[:3]}")
    
    def stage2_parsed(self, match_count: int, match_ids: List[str] = None, odds_summary: str = None):
        """Log Stage 2: Parsing completed"""
        msg = f"Parsed {match_count} matches"
        if odds_summary:
            msg += f" [{odds_summary}]"
        self.log(
            self.STAGE_PARSER,
            msg,
            {"match_count": match_count}
        )
    
    def stage2_published(self, channel: str, match_id: str, back_price: float = None):
        """Log Stage 2: Published to Redis"""
        self.log(
            self.STAGE_PARSER,
            f"Published to {channel}: {match_id}",
            {"match_id": match_id, "back_price": back_price}
        )
    
    def stage2_active_matches(self, count: int, matches: List[Dict] = None):
        """Log Stage 2: Updated active_matches"""
        odds_str = ""
        if matches:
            odds_parts = []
            for m in matches[:3]:
                name = m.get('match_name', '?')[:15]
                odds = m.get('current_odds', {})
                first_odds = list(odds.values())[0] if odds else 'N/A'
                odds_parts.append(f"{name}:{first_odds}")
            odds_str = ", ".join(odds_parts)
        self.log(
            self.STAGE_PARSER,
            f"Updated active_matches: {count} matches",
            {"match_count": count}
        )
        if odds_str:
            logger.debug(f"  Odds: {odds_str}")
    
    def stage3_received(self, match_id: str, back_price: float):
        """Log Stage 3: Received match event"""
        self.log(
            self.STAGE_CORTEX,
            f"Received event: {match_id}",
            {"match_id": match_id, "back_price": back_price}
        )
    
    def stage3_evaluated(self, strategy_count: int, signal_count: int):
        """Log Stage 3: Strategy evaluation completed"""
        self.log(
            self.STAGE_CORTEX,
            f"Evaluated {strategy_count} strategies, generated {signal_count} signals",
            {"signal_count": signal_count}
        )
    
    def stage3_signal(self, strategy: str, action: str, team: str, odds: float, confidence: float):
        """Log Stage 3: Signal generated"""
        self.log(
            self.STAGE_CORTEX,
            f"SIGNAL: [{strategy}] {action} {team} @ {odds} (conf: {confidence:.0%})",
            {"odds": odds}
        )
    
    def stage4_serving(self, match_count: int, latency_ms: float = None):
        """Log Stage 4: Serving API response"""
        self.log(
            self.STAGE_BACKEND,
            f"Serving {match_count} matches",
            {"match_count": match_count, "latency_ms": latency_ms}
        )
    
    def stage5_received(self, match_count: int, latest_update: str = None):
        """Log Stage 5: Frontend received data"""
        self.log(
            self.STAGE_FRONTEND,
            f"Received {match_count} matches",
            {"match_count": match_count}
        )


# Global instance factory
_loggers: Dict[str, FlowLogger] = {}

def get_flow_logger(component: str) -> FlowLogger:
    """Get or create a FlowLogger for a component"""
    if component not in _loggers:
        _loggers[component] = FlowLogger(component)
    return _loggers[component]


# Quick access functions for common logging
def log_stage(stage: int, component: str, message: str, data: Dict = None):
    """Quick log function"""
    get_flow_logger(component).log(stage, message, data)
