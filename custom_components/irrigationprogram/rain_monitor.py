"""Rain monitoring for irrigation program."""
from datetime import datetime, timedelta
from typing import Optional
import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_change
from homeassistant.util import dt as dt_util

from .const import (
    CONST_LIGHT_RAIN,
    CONST_HEAVY_RAIN,
    CONST_RAIN_ACCUMULATED,
)

_LOGGER = logging.getLogger(__name__)

class RainMonitor:
    """Monitor rain intensity and accumulation with support for daily resets."""
    
    def __init__(self, hass: HomeAssistant, rain_gauge: str, 
                 threshold: float, accumulation_period: int) -> None:
        """Initialize rain monitor."""
        self.hass = hass
        self._rain_gauge = rain_gauge
        self._threshold = threshold
        self._period = timedelta(hours=accumulation_period)
        self._accumulation = 0.0
        self._readings = []
        self._last_intensity = 0.0
        self._last_value = 0.0
        self._daily_accumulation = 0.0
        
        # Start monitoring rain gauge
        if self._rain_gauge:
            async_track_state_change_event(
                self.hass, [self._rain_gauge], self._handle_rain_gauge_change
            )
            # Track midnight for reset detection
            async_track_time_change(
                self.hass, 
                self._handle_midnight_reset,
                hour=0, 
                minute=0, 
                second=0
            )

    async def _handle_midnight_reset(self, now: datetime) -> None:
        """Handle daily reset at midnight."""
        _LOGGER.debug("Midnight reset detected - clearing last value")
        self._last_value = 0.0
        # Keep accumulation but mark the reset point in readings
        timestamp = dt_util.utcnow()
        self._readings.append((timestamp, 0.0, True))  # True marks a reset point
    
    async def _handle_rain_gauge_change(self, event) -> None:
        """Handle rain gauge state changes."""
        new_state = event.data.get("new_state")
        if new_state is None:
            return

        try:
            current_value = float(new_state.state)
            timestamp = dt_util.utcnow()
            
            # Detect if this is likely a reset
            if current_value < self._last_value:
                _LOGGER.debug(
                    "Rain gauge reset detected: %f -> %f", 
                    self._last_value, 
                    current_value
                )
                # Add the final value before reset to accumulation
                self._daily_accumulation += current_value
                # Mark reset in readings
                self._readings.append((timestamp, current_value, True))
            else:
                # Normal reading
                self._readings.append((timestamp, current_value, False))
            
            self._last_value = current_value
            
            # Remove old readings outside accumulation period
            cutoff_time = timestamp - self._period
            self._readings = [(ts, val, reset) for ts, val, reset in self._readings 
                            if ts > cutoff_time]
            
            # Calculate accumulation considering resets
            self._accumulation = 0.0
            prev_val = 0.0
            prev_ts = None
            
            for ts, val, is_reset in self._readings:
                if prev_ts is not None:
                    if is_reset:
                        # Reset point - start new accumulation
                        prev_val = 0.0
                    else:
                        # Normal reading - add difference if positive
                        diff = val - prev_val
                        if diff > 0:
                            self._accumulation += diff
                
                prev_val = val
                prev_ts = ts
            
            # Calculate current intensity (mm/hr)
            # Only calculate if we have 2 readings without a reset between them
            recent_readings = [(ts, val) for ts, val, reset in self._readings[-2:] 
                             if not reset]
            
            if len(recent_readings) >= 2:
                latest_ts, latest_val = recent_readings[-1]
                prev_ts, prev_val = recent_readings[-2]
                time_diff = (latest_ts - prev_ts).total_seconds() / 3600  # hours
                if time_diff > 0:
                    self._last_intensity = (latest_val - prev_val) / time_diff
            else:
                self._last_intensity = 0.0
            
        except (ValueError, TypeError):
            _LOGGER.error("Error processing rain gauge value: %s", new_state.state)
    
    @property
    def accumulation(self) -> float:
        """Return current rain accumulation."""
        return self._accumulation
    
    @property
    def intensity(self) -> float:
        """Return current rain intensity."""
        return self._last_intensity
    
    def should_prevent_watering(self) -> tuple[bool, str]:
        """Determine if watering should be prevented."""
        if self._accumulation >= self._threshold:
            return True, CONST_RAIN_ACCUMULATED
        elif self._last_intensity > 10.0:  # mm/hr
            return True, CONST_HEAVY_RAIN
        elif self._last_intensity > 2.0:   # mm/hr
            return True, CONST_LIGHT_RAIN
        return False, None 