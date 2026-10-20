// 原创城市主题插画；新增启用城市时在此登记配图，空缺时使用中性底纹。
import hangzhouWest from './assets/backdrop/hangzhou-west.svg?raw'
import hangzhouSouth from './assets/backdrop/hangzhou-south.svg?raw'
import nanjingWall from './assets/backdrop/nanjing-wall.svg?raw'
import nanjingQinhuai from './assets/backdrop/nanjing-qinhuai.svg?raw'
export interface CityArtwork { left: string; right: string; leftLabel: string; rightLabel: string }
const artworks: Record<string, CityArtwork> = {
  hangzhou: { left: hangzhouWest, right: hangzhouSouth, leftLabel: '宝石山与断桥', rightLabel: '雷峰夕照' },
  nanjing: { left: nanjingWall, right: nanjingQinhuai, leftLabel: '明城与梧桐', rightLabel: '秦淮灯影' },
}
export const cityArtwork = (city: string): CityArtwork | undefined => artworks[city]
