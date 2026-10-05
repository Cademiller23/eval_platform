/** True in the static preview build (`npm run build:demo`): the UI talks to an in-browser fake backend. */
export const IS_DEMO = import.meta.env.VITE_DEMO === "1";
