"""
TITAN Data Fingerprint Utility
Provides cross-stage data tracking and integrity verification

This module creates unique fingerprints for data at each pipeline stage,
allowing us to:
1. Track data lineage through the system
2. Detect data corruption or modification
3. Verify data integrity across stages
4. Debug data flow issues
"""

import hashlib
import json
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum
from loguru import logger


class ValidationStatus(Enum):
    """Validation result status"""
    PASS = "pass"
    FAIL = "fail"
    WARNING = "warning"
    SKIPPED = "skipped"


@dataclass
class ValidationResult:
    """Result of a data validation check"""
    status: ValidationStatus
    stage: int
    component: str
    message: str
    fingerprint: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    
    def is_valid(self) -> bool:
        """Check if validation passed"""
        return self.status in (ValidationStatus.PASS, ValidationStatus.WARNING)
    
    def to_dict(self) -> Dict:
        """Convert to dictionary for logging/storage"""
        return {
            "status": self.status.value,
            "stage": self.stage,
            "component": self.component,
            "message": self.message,
            "fingerprint": self.fingerprint,
            "details": self.details,
            "timestamp": self.timestamp
        }
    
    def __str__(self) -> str:
        status_icon = {
            ValidationStatus.PASS: "✓",
            ValidationStatus.FAIL: "✗",
            ValidationStatus.WARNING: "⚠",
            ValidationStatus.SKIPPED: "○"
        }
        return f"[{status_icon[self.status]}] Stage {self.stage} ({self.component}): {self.message}"


class DataFingerprint:
    """
    Creates and manages data fingerprints for pipeline verification.
    
    A fingerprint is a short hash of critical data fields that allows
    tracking the same data across different pipeline stages.
    """
    
    # Critical fields that define data identity
    CRITICAL_FIELDS = ['match_id', 'back_price', 'timestamp']
    
    # Additional fields to include in extended fingerprint
    EXTENDED_FIELDS = ['team1', 'team2', 'lay_price', 'is_live']
    
    def __init__(self, fingerprint_length: int = 12):
        """
        Initialize fingerprint generator
        
        Args:
            fingerprint_length: Length of the fingerprint hash (default 12 chars)
        """
        self.fingerprint_length = fingerprint_length
        self._fingerprint_cache: Dict[str, str] = {}
    
    def create(self, data: Dict, extended: bool = False) -> str:
        """
        Create a fingerprint for the given data
        
        Args:
            data: Data dictionary to fingerprint
            extended: Include extended fields in fingerprint
            
        Returns:
            Short hash string identifying this data
        """
        # Select fields to include
        fields = self.CRITICAL_FIELDS.copy()
        if extended:
            fields.extend(self.EXTENDED_FIELDS)
        
        # Extract values for selected fields
        critical_data = {}
        for field in fields:
            value = data.get(field)
            if value is not None:
                # Normalize floats to 2 decimal places for consistency
                if isinstance(value, float):
                    value = round(value, 2)
                critical_data[field] = value
        
        # Create deterministic JSON string
        json_str = json.dumps(critical_data, sort_keys=True, default=str)
        
        # Generate SHA256 hash and truncate
        hash_obj = hashlib.sha256(json_str.encode('utf-8'))
        fingerprint = hash_obj.hexdigest()[:self.fingerprint_length]
        
        return fingerprint
    
    def create_with_stage(self, data: Dict, stage: int) -> Tuple[str, Dict]:
        """
        Create fingerprint and annotate data with stage info
        
        Args:
            data: Data dictionary to fingerprint
            stage: Current pipeline stage (1-5)
            
        Returns:
            Tuple of (fingerprint, annotated_data)
        """
        fingerprint = self.create(data)
        
        # Add fingerprint metadata to data
        annotated = data.copy()
        if '_fingerprint' not in annotated:
            annotated['_fingerprint'] = {}
        
        annotated['_fingerprint'][f'stage_{stage}'] = {
            'fingerprint': fingerprint,
            'timestamp': datetime.utcnow().isoformat() + "Z"
        }
        
        return fingerprint, annotated
    
    def verify_chain(self, data: Dict, expected_stages: List[int] = None) -> List[ValidationResult]:
        """
        Verify fingerprint chain through stages
        
        Args:
            data: Data with fingerprint annotations
            expected_stages: List of stages that should have fingerprints
            
        Returns:
            List of validation results for each stage
        """
        results = []
        expected_stages = expected_stages or [1, 2, 3, 4, 5]
        
        fingerprint_data = data.get('_fingerprint', {})
        prev_fingerprint = None
        
        for stage in expected_stages:
            stage_key = f'stage_{stage}'
            
            if stage_key not in fingerprint_data:
                results.append(ValidationResult(
                    status=ValidationStatus.WARNING,
                    stage=stage,
                    component="fingerprint",
                    message=f"No fingerprint found for stage {stage}"
                ))
                continue
            
            stage_info = fingerprint_data[stage_key]
            current_fp = stage_info.get('fingerprint')
            
            # First stage establishes the baseline
            if prev_fingerprint is None:
                results.append(ValidationResult(
                    status=ValidationStatus.PASS,
                    stage=stage,
                    component="fingerprint",
                    message=f"Baseline fingerprint established",
                    fingerprint=current_fp
                ))
            # Subsequent stages should match
            elif current_fp == prev_fingerprint:
                results.append(ValidationResult(
                    status=ValidationStatus.PASS,
                    stage=stage,
                    component="fingerprint",
                    message=f"Fingerprint matches previous stage",
                    fingerprint=current_fp
                ))
            else:
                results.append(ValidationResult(
                    status=ValidationStatus.FAIL,
                    stage=stage,
                    component="fingerprint",
                    message=f"Fingerprint mismatch! Expected {prev_fingerprint}, got {current_fp}",
                    fingerprint=current_fp,
                    details={
                        "expected": prev_fingerprint,
                        "actual": current_fp
                    }
                ))
            
            prev_fingerprint = current_fp
        
        return results
    
    def compare(self, data1: Dict, data2: Dict) -> bool:
        """
        Compare fingerprints of two data objects
        
        Args:
            data1: First data dictionary
            data2: Second data dictionary
            
        Returns:
            True if fingerprints match
        """
        fp1 = self.create(data1)
        fp2 = self.create(data2)
        return fp1 == fp2
    
    @staticmethod
    def format_for_log(fingerprint: str) -> str:
        """Format fingerprint for logging"""
        return f"[FP:{fingerprint}]"


class FingerprintTracker:
    """
    Tracks fingerprints across the entire pipeline for debugging
    and data lineage verification.
    """
    
    def __init__(self, max_history: int = 1000):
        """
        Initialize tracker
        
        Args:
            max_history: Maximum number of fingerprints to track
        """
        self.max_history = max_history
        self._history: List[Dict] = []
        self._by_fingerprint: Dict[str, List[Dict]] = {}
        self.fingerprinter = DataFingerprint()
    
    def record(self, stage: int, component: str, data: Dict, 
               validation_result: Optional[ValidationResult] = None) -> str:
        """
        Record a fingerprint observation
        
        Args:
            stage: Pipeline stage (1-5)
            component: Component name
            data: Data being processed
            validation_result: Optional validation result
            
        Returns:
            The fingerprint for this data
        """
        fingerprint = self.fingerprinter.create(data)
        
        record = {
            "fingerprint": fingerprint,
            "stage": stage,
            "component": component,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "match_id": data.get("match_id"),
            "back_price": data.get("back_price"),
            "validation": validation_result.to_dict() if validation_result else None
        }
        
        # Add to history
        self._history.append(record)
        if len(self._history) > self.max_history:
            removed = self._history.pop(0)
            # Clean up by-fingerprint index
            old_fp = removed.get("fingerprint")
            if old_fp in self._by_fingerprint:
                self._by_fingerprint[old_fp] = [
                    r for r in self._by_fingerprint[old_fp]
                    if r["timestamp"] != removed["timestamp"]
                ]
        
        # Index by fingerprint
        if fingerprint not in self._by_fingerprint:
            self._by_fingerprint[fingerprint] = []
        self._by_fingerprint[fingerprint].append(record)
        
        return fingerprint
    
    def get_lineage(self, fingerprint: str) -> List[Dict]:
        """
        Get the complete lineage of a fingerprint through the pipeline
        
        Args:
            fingerprint: The fingerprint to trace
            
        Returns:
            List of records for this fingerprint, ordered by stage
        """
        records = self._by_fingerprint.get(fingerprint, [])
        return sorted(records, key=lambda r: (r["stage"], r["timestamp"]))
    
    def get_validation_summary(self) -> Dict:
        """
        Get summary of validation results
        
        Returns:
            Dictionary with pass/fail counts by stage
        """
        summary = {
            "total_records": len(self._history),
            "by_stage": {},
            "failures": []
        }
        
        for record in self._history:
            stage = record["stage"]
            if stage not in summary["by_stage"]:
                summary["by_stage"][stage] = {"pass": 0, "fail": 0, "warning": 0}
            
            validation = record.get("validation")
            if validation:
                status = validation.get("status", "pass")
                if status in summary["by_stage"][stage]:
                    summary["by_stage"][stage][status] += 1
                
                if status == "fail":
                    summary["failures"].append({
                        "fingerprint": record["fingerprint"],
                        "stage": stage,
                        "message": validation.get("message"),
                        "timestamp": record["timestamp"]
                    })
        
        return summary
    
    def clear(self):
        """Clear all tracking history"""
        self._history.clear()
        self._by_fingerprint.clear()


# Global instances
_fingerprinter = DataFingerprint()
_tracker = FingerprintTracker()


def create_fingerprint(data: Dict, extended: bool = False) -> str:
    """Create a fingerprint for data"""
    return _fingerprinter.create(data, extended)


def get_tracker() -> FingerprintTracker:
    """Get the global fingerprint tracker"""
    return _tracker


def track_data(stage: int, component: str, data: Dict, 
               validation_result: Optional[ValidationResult] = None) -> str:
    """
    Track data at a pipeline stage
    
    Args:
        stage: Pipeline stage (1-5)
        component: Component name
        data: Data being processed
        validation_result: Optional validation result
        
    Returns:
        The fingerprint for this data
    """
    return _tracker.record(stage, component, data, validation_result)


def verify_data_lineage(fingerprint: str) -> List[Dict]:
    """Get the lineage of a fingerprint through the pipeline"""
    return _tracker.get_lineage(fingerprint)


def get_validation_summary() -> Dict:
    """Get summary of all validations"""
    return _tracker.get_validation_summary()
