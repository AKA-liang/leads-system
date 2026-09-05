#!/bin/bash
# 每 5 分钟健康自检: 服务挂了自动拉起并记录
LOG=/var/log/watchdog.log
restart() {
  echo "$(date '+%F %T') $1 异常,尝试拉起" >> $LOG
  systemctl restart $1 2>/dev/null || true
}
systemctl is-active --quiet nginx || restart nginx
systemctl is-active --quiet leads-web || restart leads-web
systemctl is-active --quiet mihomo || restart mihomo
systemctl --user is-active --quiet openclaw-gateway.service || {
  echo "$(date '+%F %T') openclaw-gateway 异常,尝试拉起" >> $LOG
  systemctl --user restart openclaw-gateway.service 2>/dev/null || true
}
# 内存水位记录(超 85% 记一笔,便于复盘)
MEM=$(free | awk '/Mem:/{printf "%d", $3/$2*100}')
if [ "$MEM" -gt 85 ]; then
  echo "$(date '+%F %T') 内存高水位 ${MEM}%" >> $LOG
fi
