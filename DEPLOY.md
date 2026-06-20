# Deploy to Vercel

## Step 1: Push your code to GitHub/GitLab/Bitbucket

First, push your project to a git repository on one of these platforms!

## Step 2: Sign up / Log in to Vercel

Go to [https://vercel.com](https://vercel.com) and log in or create an account!

## Step 3: Import your project to Vercel

1. On Vercel dashboard, click "Add New" → "Project"
2. Import your git repository (make sure to choose the right one!)
3. On the "Configure Project" screen:

### Project Settings
- **Framework Preset**: Vite (should be detected automatically!)
- **Root Directory**: (leave blank, use project root)
- **Build Command**: `pnpm run build`
- **Output Directory**: `artifacts/video-finder/dist` (should be detected!)

### Environment Variables
Add these environment variables from your `artifacts/api-server/.env` file!

- `SUPABASE_URL` (from your .env)
- `SUPABASE_KEY` (from your .env)
- `RAPIDAPI_KEY` (from your .env)
- `GOOGLE_API_KEY` (from your .env, optional)
- `GOOGLE_CSE_ID` (from your .env, optional)

## Step 4: Deploy!

Click "Deploy"! Vercel will now build and deploy your app!

## Important Notes:

### Playwright Limitation on Vercel
Vercel Functions have a max execution time limit (Pro plan: up to 5 minutes, Hobby: 10 seconds)! Playwright scraping might hit these limits for large Excel files! For best scraping results, you may want to:
1. Keep Excel sheets small (fewer than ~20 product URLs at a time)
2. Use a paid Vercel plan for longer execution time
3. Or use a dedicated service like Render or Railway for your backend!

## Step 5: After Deployment!

Once deployed, Vercel will give you a URL like `https://your-project-name.vercel.app`! You can share this with others!
