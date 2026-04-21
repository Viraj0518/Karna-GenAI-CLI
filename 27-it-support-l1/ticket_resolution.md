# Ticket Resolution: Privy Session Cookie Reset

**Issue:** User cannot access their vault due to expired or corrupted session cookies.

---

## Step-by-Step Resolution

### Step 1: Clear Browser Cache and Cookies
1. Close all browser tabs
2. Go to **Settings** → **Privacy & Security**
3. Click **Clear browsing data**
4. Select:
   - Time range: **All time**
   - ☑ Cookies and other site data
   - ☑ Cached images and files
5. Click **Clear data**
6. Close and reopen your browser

### Step 2: Verify You're Using a Supported Browser
- Chrome (v90+)
- Firefox (v88+)
- Safari (v14+)
- Edge (v90+)

**If using a different browser:** Recommend switching to one of the above.

### Step 3: Log Out Completely from Privy
1. Navigate to `https://vault.privy.com`
2. Click your profile icon (top-right)
3. Select **Sign Out**
4. Wait for the page to redirect to the login screen

### Step 4: Log Back In
1. Enter your email address
2. Complete MFA (multi-factor authentication)
3. **Allow popup if prompted** for authentication window
4. Wait for vault to fully load (typically 10–15 seconds)

---

## Verification Checklist

After completing all steps, verify:

- [ ] Can access the vault dashboard
- [ ] Can view at least one vault item
- [ ] No "Session Expired" error messages
- [ ] Browser tab remains open without reload loops

---

## Escalation Trigger

**If Step 4 still fails after 2 attempts:**
- Note the exact error message (screenshot if possible)
- Escalate to **L2 Support** with ticket reference
- Provide: browser name, OS version, and timestamp of last failed attempt

---

## Quick Troubleshooting

| Issue | Solution |
|-------|----------|
| "Session Expired" appears after login | Repeat Steps 1–4; contact L2 if persists |
| MFA not receiving codes | Check spam folder; verify phone number in account settings |
| Vault loads but shows "Access Denied" | Account may be suspended; escalate to L2 |
| Browser freezes during login | Disable browser extensions and retry Step 4 |

---

## Contact L2 Support

**If issue unresolved after Step 4:** 
- Email: `l2-support@karna.internal`
- Include ticket number, error message, and this walkthrough's completion status
