# Security Checklist for Public Repository

**Status**: ⏳ IN PROGRESS

Follow this checklist to safely prepare your repository for public release.

---

## 1. Revoke Compromised Secrets ✅

### Gemini API Key
- [x] Revoked key: `YOUR_REVOKED_GEMINI_KEY`
- [x] Deleted from Google AI Studio

### Supabase Keys
- [x] Migrated from legacy JWT model to new API key model
- [x] New publishable key: `sb_publishable_aGj614k3HmSwQw_B00lndA_BQnYY64_`
- [x] New secret key: `sb_secret_...` (keep this secret!)

---

## 2. Update Environment Variables ⏳

### Check JWT Secret Status

**ACTION REQUIRED**: Visit your Supabase dashboard to check:

```bash
# Go to: https://supabase.com/dashboard/project/azkrxuiqcpxmsgydlyun/settings/api
# Look for "Legacy API Keys" section
# Check if "JWT Secret" is still available
```

- [ ] JWT Secret still available → Use backward-compatible Option 1
- [ ] JWT Secret removed → Use JWKS/RS256 Option 2

See [MIGRATION_GUIDE.md](MIGRATION_GUIDE.md) for detailed instructions.

### Update Local Environment Files

**Backend** (`deployment/.env`):
```bash
# Copy from .env.example and fill in:
GEMINI_API_KEY=your-new-gemini-api-key
SUPABASE_URL=https://azkrxuiqcpxmsgydlyun.supabase.co
SUPABASE_SERVICE_KEY=sb_secret_YOUR_NEW_SECRET_KEY

# Optional - only if JWT secret still available:
# SUPABASE_JWT_SECRET=your-jwt-secret
```

**Frontend** (`console/.env`):
```bash
# Copy from .env.example and fill in:
VITE_API_BASE_URL=http://localhost:8000
VITE_GATEWAY_BASE_URL=http://localhost:3000
VITE_SUPABASE_URL=https://azkrxuiqcpxmsgydlyun.supabase.co
VITE_SUPABASE_ANON_KEY=sb_publishable_aGj614k3HmSwQw_B00lndA_BQnYY64_
VITE_DEV_MODE=false
```

- [ ] Updated `deployment/.env`
- [ ] Updated `console/.env`
- [ ] Tested authentication locally

---

## 3. Update Code (If JWT Secret Removed) ⏳

**Only required if Supabase no longer provides JWT secret**

Update `management_plane/app/auth.py` to use JWKS/RS256 instead of HS256.

See [MIGRATION_GUIDE.md](MIGRATION_GUIDE.md) Option 2 for code changes.

- [ ] Updated JWT validation code
- [ ] Tested authentication with new method
- [ ] All tests passing: `pytest management_plane/tests/test_auth.py`

---

## 4. Fix .gitignore ✅

- [x] Added `.env` protection to `.gitignore`
- [x] Updated `deployment/.env.example` with placeholders
- [x] Updated `console/.env.example` with placeholders

---

## 5. Clean Git History ⏳

**CRITICAL**: The old Gemini API key and Supabase JWT secret are still in git history!

### Option A: Automated Script (Recommended)

```bash
# Run the cleanup script
./cleanup-git-history.sh
```

This will:
- Create a backup branch
- Remove all `.env` files from history
- Clean up git references
- Verify no secrets remain

### Option B: Manual Cleanup

See [MIGRATION_GUIDE.md](MIGRATION_GUIDE.md) for manual `git-filter-repo` commands.

### Verification

After cleanup, verify:

```bash
# 1. No .env files in history
git log --all --oneline --name-only --full-history | grep "\.env$"
# Should return nothing

# 2. No old secrets in history
git log --all -S "YOUR_REVOKED_GEMINI_KEY"
# Should return nothing

# 3. Check current status
git status
# Should not show any .env files
```

- [ ] Ran cleanup script
- [ ] Verified no .env files in history
- [ ] Verified no secrets in history
- [ ] Created backup branch

---

## 6. Remove Untracked .env Files ⏳

These files are currently untracked but should be moved:

```bash
# Move to .env.local (gitignored)
mv deployment/.env deployment/.env.local
mv console/.env console/.env.local

# Or keep them if you need for local dev
# (they're now gitignored)
```

- [ ] Handled untracked `.env` files
- [ ] Verified with `git status` - no `.env` files shown

---

## 7. Final Verification ⏳

### Security Checks

```bash
# Install truffleHog (optional but recommended)
docker run --rm -v $(pwd):/repo trufflesecurity/trufflehog:latest git file:///repo

# Or manual checks:
git log --all -S "api.*key" -i --oneline | head -20
git log --all -S "secret" -i --oneline | head -20
```

### Test the Application

```bash
# 1. Test backend
cd management_plane
GEMINI_API_KEY=your-new-key \
SUPABASE_URL=https://azkrxuiqcpxmsgydlyun.supabase.co \
SUPABASE_SERVICE_KEY=sb_secret_your-key \
pytest tests/ -v

# 2. Test frontend
cd console
npm run dev
# Try logging in

# 3. Test full stack
cd deployment
./deploy-local.sh
```

- [ ] Backend tests passing
- [ ] Frontend authentication working
- [ ] Full stack deployment successful
- [ ] No security warnings from scanners

---

## 8. Push to New Public Repository ⏳

**DO NOT push to the old remote!** Create a new public repository instead.

```bash
# 1. Create new repo on GitHub/GitLab
# (Don't initialize with README - we have our own)

# 2. Add new remote
git remote add public <new-repo-url>

# 3. Push
git push public main --force

# 4. Set new remote as default (optional)
git remote rename origin old-origin
git remote rename public origin
```

- [ ] Created new public repository
- [ ] Added new remote
- [ ] Pushed to new remote
- [ ] Verified repository is public
- [ ] Added repository description and tags

---

## 9. Post-Push Verification ⏳

Visit your new public repository and check:

- [ ] No `.env` files visible in repository
- [ ] No secrets in commit history (use GitHub secret scanning)
- [ ] `.env.example` files contain only placeholders
- [ ] README.md displays correctly
- [ ] Documentation is up to date

---

## 10. Cleanup ⏳

After successful public push:

```bash
# 1. Remove this checklist (or keep for reference)
rm SECURITY_CHECKLIST.md
rm cleanup-git-history.sh

# 2. Update README with public repo link
# (if needed)

# 3. Consider adding:
# - SECURITY.md with responsible disclosure policy
# - CONTRIBUTING.md with contribution guidelines
# - LICENSE file
```

- [ ] Removed security artifacts
- [ ] Updated documentation
- [ ] Added community files (LICENSE, CONTRIBUTING, etc.)

---

## Summary

**Current Status**:

- ✅ Secrets revoked
- ✅ .gitignore fixed
- ✅ .env.example files updated
- ⏳ Environment variables need updating
- ⏳ Git history needs cleaning
- ⏳ Code may need JWT migration
- ⏳ Final verification pending
- ⏳ Public push pending

**Estimated Time Remaining**: 20-30 minutes

**Critical Issues**:
1. Git history contains old secrets - **MUST clean before push**
2. Check if JWT secret migration is needed

**Blockers**:
- Need to verify JWT secret availability in Supabase dashboard
- Need to run git history cleanup script

---

## Questions or Issues?

If you encounter any problems:

1. Check [MIGRATION_GUIDE.md](MIGRATION_GUIDE.md) for detailed instructions
2. Verify secrets are revoked before pushing
3. Test locally before pushing to public
4. When in doubt, ask before pushing!

**Remember**: Once pushed publicly, assume all secrets in history are compromised.
