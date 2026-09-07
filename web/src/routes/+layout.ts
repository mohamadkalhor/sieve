// Static build with an SPA fallback: FastAPI serves web/build and every deep
// link lands on the shell, so there is no Node server in the deployment.
export const prerender = false;
export const ssr = false;
