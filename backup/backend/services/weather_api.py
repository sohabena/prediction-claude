"""
Weather API Integration
Real-time weather conditions for cricket match venues

The Oracle (Betting Expert): "Weather is a game-changer. Rain delays, dew factor,
cloud cover—each affects odds by 10-30%. We must track weather in real-time."
"""

import aiohttp
import asyncio
from typing import Dict, Optional
from datetime import datetime
from loguru import logger
from dataclasses import dataclass
import os
from dotenv import load_dotenv

load_dotenv()


@dataclass
class WeatherCondition:
    """Current weather conditions at a venue"""
    venue: str
    city: str
    
    # Current conditions
    temperature_celsius: float
    humidity_percent: float
    wind_speed_kmh: float
    cloud_cover_percent: float
    
    # Cricket-specific factors
    precipitation_probability: float  # % chance of rain
    precipitation_mm: float  # Current rainfall
    visibility_km: float
    
    # Derived insights
    dew_factor: str  # "low", "moderate", "high"
    batting_conditions: str  # "excellent", "good", "difficult"
    
    # Metadata
    timestamp: datetime
    
    @property
    def is_rain_threat(self) -> bool:
        """Check if rain is likely (> 30% chance or active rainfall)"""
        return self.precipitation_probability > 30 or self.precipitation_mm > 0
    
    @property
    def is_batting_friendly(self) -> bool:
        """Good batting conditions: low humidity, no rain, good visibility"""
        return (
            self.humidity_percent < 70 and
            not self.is_rain_threat and
            self.visibility_km > 5 and
            self.cloud_cover_percent < 70
        )
    
    @property
    def swing_bowling_conditions(self) -> str:
        """Estimate swing bowling assistance"""
        # Cloud cover + humidity = swing bowling
        if self.cloud_cover_percent > 70 and self.humidity_percent > 70:
            return "high_swing"
        elif self.cloud_cover_percent > 50 or self.humidity_percent > 60:
            return "moderate_swing"
        return "low_swing"


class OpenWeatherMapAPI:
    """
    OpenWeatherMap API Integration
    
    Features:
    - Current weather conditions
    - Hourly forecasts (next 48 hours)
    - Weather alerts
    
    Rate Limiting: 60 calls/minute (free tier)
    """
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv('OPENWEATHER_API_KEY', '')
        self.base_url = "https://api.openweathermap.org/data/2.5"
        self.session: Optional[aiohttp.ClientSession] = None
        
        # Cache for venues (city -> coordinates)
        self.venue_cache: Dict[str, Dict[str, float]] = {
            "Mumbai": {"lat": 19.0760, "lon": 72.8777},
            "Delhi": {"lat": 28.7041, "lon": 77.1025},
            "Bangalore": {"lat": 12.9716, "lon": 77.5946},
            "Kolkata": {"lat": 22.5726, "lon": 88.3639},
            "Chennai": {"lat": 13.0827, "lon": 80.2707},
            "Hyderabad": {"lat": 17.3850, "lon": 78.4867},
            "Pune": {"lat": 18.5204, "lon": 73.8567},
            "Ahmedabad": {"lat": 23.0225, "lon": 72.5714},
            "Jaipur": {"lat": 26.9124, "lon": 75.7873},
            "Mohali": {"lat": 30.7046, "lon": 76.7179},
            "Dubai": {"lat": 25.2048, "lon": 55.2708},
            "Abu Dhabi": {"lat": 24.4539, "lon": 54.3773},
            "Sharjah": {"lat": 25.3463, "lon": 55.4209},
            "London": {"lat": 51.5074, "lon": -0.1278},
            "Melbourne": {"lat": -37.8136, "lon": 144.9631},
            "Sydney": {"lat": -33.8688, "lon": 151.2093},
        }
        
        if not self.api_key:
            logger.warning("⚠️  OPENWEATHER_API_KEY not set. Weather data will be unavailable.")
    
    async def __aenter__(self):
        """Async context manager entry"""
        self.session = aiohttp.ClientSession()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        if self.session:
            await self.session.close()
    
    async def _get_coordinates(self, city: str) -> Optional[Dict[str, float]]:
        """Get coordinates for a city (cached)"""
        # Check cache first
        if city in self.venue_cache:
            return self.venue_cache[city]
        
        # Geocoding API to get coordinates
        if not self.session:
            self.session = aiohttp.ClientSession()
        
        try:
            url = f"http://api.openweathermap.org/geo/1.0/direct"
            params = {
                "q": city,
                "limit": 1,
                "appid": self.api_key
            }
            
            async with self.session.get(url, params=params, timeout=5) as response:
                if response.status == 200:
                    data = await response.json()
                    if data:
                        coords = {"lat": data[0]['lat'], "lon": data[0]['lon']}
                        self.venue_cache[city] = coords
                        return coords
        
        except Exception as e:
            logger.error(f"❌ Failed to geocode {city}: {e}")
        
        return None
    
    async def get_current_weather(self, city: str) -> Optional[WeatherCondition]:
        """
        Get current weather conditions for a city
        
        Args:
            city: City name or venue location
        
        Returns:
            WeatherCondition with current data
        """
        if not self.api_key:
            logger.warning("⚠️  OpenWeatherMap API key not configured")
            return None
        
        # Get coordinates
        coords = await self._get_coordinates(city)
        if not coords:
            logger.error(f"❌ Could not find coordinates for {city}")
            return None
        
        if not self.session:
            self.session = aiohttp.ClientSession()
        
        try:
            url = f"{self.base_url}/weather"
            params = {
                "lat": coords['lat'],
                "lon": coords['lon'],
                "appid": self.api_key,
                "units": "metric"
            }
            
            async with self.session.get(url, params=params, timeout=10) as response:
                if response.status == 200:
                    data = await response.json()
                    
                    # Parse weather data
                    main = data.get('main', {})
                    clouds = data.get('clouds', {})
                    wind = data.get('wind', {})
                    rain = data.get('rain', {})
                    
                    temperature = main.get('temp', 25.0)
                    humidity = main.get('humidity', 50.0)
                    cloud_cover = clouds.get('all', 50.0)
                    
                    # Calculate dew factor (humidity + temperature)
                    dew_factor = "low"
                    if humidity > 75 and temperature > 25:
                        dew_factor = "high"
                    elif humidity > 60 and temperature > 20:
                        dew_factor = "moderate"
                    
                    # Determine batting conditions
                    batting_conditions = "good"
                    if rain.get('1h', 0) > 0:
                        batting_conditions = "difficult"
                    elif humidity < 60 and cloud_cover < 50:
                        batting_conditions = "excellent"
                    elif humidity > 80 or cloud_cover > 80:
                        batting_conditions = "difficult"
                    
                    weather = WeatherCondition(
                        venue=city,
                        city=city,
                        temperature_celsius=temperature,
                        humidity_percent=humidity,
                        wind_speed_kmh=wind.get('speed', 0) * 3.6,  # m/s to km/h
                        cloud_cover_percent=cloud_cover,
                        precipitation_probability=0,  # Not available in current weather
                        precipitation_mm=rain.get('1h', 0),
                        visibility_km=data.get('visibility', 10000) / 1000,
                        dew_factor=dew_factor,
                        batting_conditions=batting_conditions,
                        timestamp=datetime.utcnow()
                    )
                    
                    logger.info(f"🌤️  {city}: {temperature:.1f}°C, {humidity:.0f}% humidity, {batting_conditions} batting")
                    return weather
                
                elif response.status == 429:
                    logger.error("⚠️  Rate limit exceeded for OpenWeatherMap")
                else:
                    logger.error(f"❌ Weather API error: {response.status}")
        
        except asyncio.TimeoutError:
            logger.error(f"❌ Weather API timeout for {city}")
        except Exception as e:
            logger.error(f"❌ Weather API request failed: {e}")
        
        return None
    
    async def get_hourly_forecast(self, city: str, hours: int = 12) -> Optional[list]:
        """
        Get hourly weather forecast
        
        The Math (Quant Analyst): "Forecast data lets us predict rain delays
        and adjust in-play strategies proactively."
        
        Args:
            city: City name
            hours: Number of hours to forecast (max 48)
        
        Returns:
            List of hourly weather forecasts
        """
        coords = await self._get_coordinates(city)
        if not coords:
            return None
        
        if not self.session:
            self.session = aiohttp.ClientSession()
        
        try:
            # Note: Hourly forecast requires paid subscription
            # Free tier only has 5-day/3-hour forecast
            url = f"{self.base_url}/forecast"
            params = {
                "lat": coords['lat'],
                "lon": coords['lon'],
                "appid": self.api_key,
                "units": "metric",
                "cnt": min(hours // 3, 16)  # 3-hour intervals
            }
            
            async with self.session.get(url, params=params, timeout=10) as response:
                if response.status == 200:
                    data = await response.json()
                    forecasts = []
                    
                    for item in data.get('list', []):
                        dt = datetime.fromtimestamp(item['dt'])
                        temp = item['main']['temp']
                        humidity = item['main']['humidity']
                        rain_prob = item.get('pop', 0) * 100  # Probability of precipitation
                        rain_mm = item.get('rain', {}).get('3h', 0)
                        
                        forecasts.append({
                            "time": dt,
                            "temperature": temp,
                            "humidity": humidity,
                            "rain_probability": rain_prob,
                            "rainfall_mm": rain_mm
                        })
                    
                    logger.info(f"📅 Got {len(forecasts)} forecast intervals for {city}")
                    return forecasts
        
        except Exception as e:
            logger.error(f"❌ Forecast API failed: {e}")
        
        return None


class WeatherImpactAnalyzer:
    """
    Analyze weather impact on betting odds
    
    The Oracle (Betting Expert): "Weather-based odds adjustments:
    - Rain threat: Back the favorite (match shortened)
    - High dew: Bowling team disadvantage (bet on batsmen)
    - Overcast: Bowling advantage (under bets)
    """
    
    @staticmethod
    def calculate_weather_impact(weather: WeatherCondition) -> Dict[str, float]:
        """
        Calculate weather impact scores for betting
        
        Returns:
            Dict with impact scores for different bet types
        """
        impact = {
            "rain_delay_risk": 0.0,  # 0-100
            "batting_advantage": 50.0,  # 0-100 (50 = neutral)
            "bowling_advantage": 50.0,  # 0-100
            "total_runs_adjustment": 0.0,  # +/- runs from baseline
        }
        
        # Rain delay risk
        if weather.is_rain_threat:
            impact["rain_delay_risk"] = weather.precipitation_probability
        
        # Batting advantage
        if weather.is_batting_friendly:
            impact["batting_advantage"] = 70.0
            impact["total_runs_adjustment"] = +10
        elif weather.humidity_percent > 80:
            impact["batting_advantage"] = 30.0
            impact["total_runs_adjustment"] = -15
        
        # Bowling advantage (swing conditions)
        if weather.swing_bowling_conditions == "high_swing":
            impact["bowling_advantage"] = 75.0
            impact["total_runs_adjustment"] = -20
        elif weather.swing_bowling_conditions == "moderate_swing":
            impact["bowling_advantage"] = 60.0
            impact["total_runs_adjustment"] = -10
        
        # Dew factor (evening matches)
        if weather.dew_factor == "high":
            impact["batting_advantage"] = 80.0  # Heavy dew = batting paradise
            impact["bowling_advantage"] = 20.0
            impact["total_runs_adjustment"] = +15
        
        return impact
    
    @staticmethod
    def generate_weather_insights(weather: WeatherCondition) -> list:
        """
        Generate human-readable weather insights for betting
        
        Returns:
            List of actionable insights
        """
        insights = []
        
        if weather.is_rain_threat:
            insights.append(f"⚠️ Rain threat ({weather.precipitation_probability:.0f}%) - Back favorites (DLS favors team ahead)")
        
        if weather.dew_factor == "high":
            insights.append(f"💧 Heavy dew expected - Batting advantage in 2nd innings")
        
        if weather.swing_bowling_conditions == "high_swing":
            insights.append(f"☁️ Overcast conditions - High swing bowling, difficult batting")
        
        if weather.is_batting_friendly:
            insights.append(f"☀️ Excellent batting conditions - Expect high scores")
        
        if weather.wind_speed_kmh > 25:
            insights.append(f"💨 Windy conditions ({weather.wind_speed_kmh:.0f} km/h) - Fielding challenges")
        
        return insights


# Global weather API instance
_weather_api: Optional[OpenWeatherMapAPI] = None

def get_weather_api() -> OpenWeatherMapAPI:
    """Get or create global weather API instance"""
    global _weather_api
    if _weather_api is None:
        _weather_api = OpenWeatherMapAPI()
    return _weather_api


# Example usage
if __name__ == "__main__":
    async def test_weather_api():
        """Test weather API integration"""
        api = OpenWeatherMapAPI()
        
        async with api:
            # Test major cricket venues
            cities = ["Mumbai", "Dubai", "Melbourne"]
            
            for city in cities:
                print(f"\n🏏 Weather for {city}:")
                
                weather = await api.get_current_weather(city)
                if weather:
                    print(f"   Temperature: {weather.temperature_celsius:.1f}°C")
                    print(f"   Humidity: {weather.humidity_percent:.0f}%")
                    print(f"   Conditions: {weather.batting_conditions}")
                    print(f"   Dew Factor: {weather.dew_factor}")
                    print(f"   Swing Conditions: {weather.swing_bowling_conditions}")
                    
                    # Get weather impact
                    impact = WeatherImpactAnalyzer.calculate_weather_impact(weather)
                    print(f"\n   Impact Analysis:")
                    print(f"   - Rain Risk: {impact['rain_delay_risk']:.0f}%")
                    print(f"   - Batting Advantage: {impact['batting_advantage']:.0f}/100")
                    print(f"   - Runs Adjustment: {impact['total_runs_adjustment']:+.0f}")
                    
                    # Get insights
                    insights = WeatherImpactAnalyzer.generate_weather_insights(weather)
                    if insights:
                        print(f"\n   Insights:")
                        for insight in insights:
                            print(f"   {insight}")
    
    # Run test
    asyncio.run(test_weather_api())

