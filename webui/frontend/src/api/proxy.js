import http from './request'

// 代理连通性测试（后端并发测试，可能耗时，单独放宽超时到 3 分钟）
export const testProxies = (proxies, timeout = 8) =>
  http.post('/api/proxy/test', { proxies, timeout }, { timeout: 180000 })

// 同步代理池到后端（供定时健康检查使用）
export const syncProxyPool = (proxies) =>
  http.post('/api/proxy/pool', { proxies })

// 查询代理健康状态（前端仪表盘绿色/红色指示灯展示）
export const getProxyHealth = () =>
  http.get('/api/proxy/health')

// 手动触发一次代理健康检查
export const triggerHealthCheck = () =>
  http.post('/api/proxy/health/check')