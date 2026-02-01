# 🔒 安全性说明

## 环境变量配置

### ⚠️ 重要提示

**请勿将包含敏感信息的 `.env` 文件提交到 Git 仓库！**

本项目使用 `.env` 文件管理敏感配置，包括：
- RPC 节点 API 密钥
- 数据库连接字符串
- 其他敏感配置

### 配置步骤

1. **复制环境变量模板**
   ```bash
   cp .env.example .env
   ```

2. **编辑 `.env` 文件**
   - 将 `POLYGON_RPC_URL` 替换为你的专属 RPC 节点地址
   - 根据需要调整其他配置

3. **验证 `.gitignore`**
   - 确认 `.env` 已添加到 `.gitignore`
   - 运行 `git status` 确保 `.env` 不会被跟踪

### RPC 节点获取

推荐的 Polygon RPC 提供商：

- **Chainstack** (推荐)
  - 注册: https://chainstack.com
  - 免费额度: 300万请求/月
  - URL 格式: `https://polygon-mainnet.core.chainstack.com/YOUR_API_KEY`

- **Alchemy**
  - 注册: https://www.alchemy.com
  - 免费额度: 300万计算单元/月
  - URL 格式: `https://polygon-mainnet.g.alchemy.com/v2/YOUR_API_KEY`

- **Infura**
  - 注册: https://infura.io
  - 免费额度: 100k 请求/天
  - URL 格式: `https://polygon-mainnet.infura.io/v3/YOUR_API_KEY`

### 安全最佳实践

✅ **应该做的：**
- 使用 `.env` 文件存储所有敏感信息
- 定期轮换 API 密钥
- 为不同环境使用不同的配置文件（`.env.development`, `.env.production`）
- 限制 API 密钥的访问权限和速率

❌ **不应该做的：**
- 在代码中硬编码 API 密钥
- 将 `.env` 文件提交到版本控制系统
- 在公共论坛或聊天中分享 API 密钥
- 使用生产环境的密钥进行本地开发

### 泄露应对

如果不慎泄露了 API 密钥：

1. **立即撤销/删除泄露的密钥**
2. **生成新的密钥**
3. **更新 `.env` 文件**
4. **检查是否有未授权使用**
5. **如果已提交到 Git，使用 `git-filter-repo` 或 `BFG Repo-Cleaner` 清理历史**

### Git 历史清理

如果已经提交了包含密钥的文件：

```bash
# 使用 git-filter-repo (推荐)
pip install git-filter-repo
git filter-repo --invert-paths --path .env

# 或使用 BFG Repo-Cleaner
java -jar bfg.jar --delete-files .env
```

⚠️ **注意**: 清理 Git 历史会改变提交哈希，需要强制推送。

## 依赖安全

定期更新依赖以修复安全漏洞：

```bash
# 检查过期依赖
pip list --outdated

# 更新依赖
pip install --upgrade -r requirements.txt

# 安全审计
pip install safety
safety check
```

## 报告安全问题

如发现安全漏洞，请通过以下方式报告：
- 创建 GitHub Issue（标记为 security）
- 或发送邮件至项目维护者

请勿公开披露未修复的漏洞。
