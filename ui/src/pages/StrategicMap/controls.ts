const base = "inline-flex min-h-11 items-center justify-center gap-2 rounded-lg border px-4 py-2 text-sm font-medium focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600 disabled:cursor-not-allowed";

// Keep each variant's colors exclusive. Appended important utilities can lose
// to the global important background rules in the production stylesheet.
export const secondaryButton = `${base} border-slate-300 bg-white text-slate-700 hover:bg-slate-50 disabled:bg-slate-100 disabled:text-slate-500`;
export const primaryButton = `${base} border-blue-700 bg-blue-700 text-white hover:bg-blue-800 disabled:border-slate-300 disabled:bg-slate-200 disabled:text-slate-600`;
