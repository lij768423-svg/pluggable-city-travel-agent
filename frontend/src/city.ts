import type { CityInfo } from './api'
export const legacyCity: CityInfo = {
  key: 'hangzhou', name: '杭州', brand: '杭州行记',
  map_center: { lat: 30.25, lon: 120.16 },
  default_origin: { name: '湖滨', lat: 30.25, lon: 120.17 },
}
export const cityTagline = (city: CityInfo) => city.key === 'hangzhou' ? 'HANGZHOU, YOUR WAY.' : 'YOUR CITY, YOUR WAY.'
