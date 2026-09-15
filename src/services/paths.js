/* Where the admin portal lives.

   Not `/admin`, which is the first thing anybody curious types, and not a word
   at all — a random path, so nobody arrives there by guessing or by accident.
   Set `VITE_ADMIN_PATH` to use a different one in a deployment, and change it
   whenever it has been shared somewhere it should not have been.

   This is a curtain, not a lock. The path ships in the site's JavaScript like
   every other route, so anybody who reads the bundle can find it. What keeps
   the portal closed is the `is_admin` column: the router sends everyone else
   away, and `/api/admin` answers 404 to anybody it does not name — signed in
   or not. */

const configured = (import.meta.env.VITE_ADMIN_PATH || '').trim();

export const ADMIN_PATH = configured
  ? `/${configured.replace(/^\/+|\/+$/g, '')}`
  : '/desk-5pngn47wv5na';
