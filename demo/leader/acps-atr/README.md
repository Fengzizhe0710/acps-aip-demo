# acps-atr

注册材料目录：

```text
acps-atr/
  acs.json.example   # 模板（提交到仓库）
  acs.json           # 本地副本（git 忽略，从 example 复制）
  certs/             # 公钥证书：{aic}.crt、ca.crt
  private/           # 私钥：{aic}.key
```

首次使用前：

```bash
cp acs.json.example acs.json
```
