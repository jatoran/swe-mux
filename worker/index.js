/**
 * swemux.dev's Worker entry point. It exists to count one thing.
 *
 * `wrangler.jsonc` used to have no `main` at all: every request was answered
 * from `site/` by Workers Assets and no script ran. That comment predicted this
 * file ("if server-side logic is ever wanted here, it arrives as a `main` entry
 * point") and this is the first thing to want it.
 *
 * **What it counts, and why that is the only interesting number available.**
 * Every install polls `https://swemux.dev/version.json` once a day
 * (`src/swe_mux/update_check.py`), so requests to that one path are the closest
 * thing this project has to a daily-active-installs figure. PyPI downloads count
 * mirrors and CI; GitHub release asset counts are cumulative and measure a click
 * rather than a run. Neither says whether anyone still has swe-mux running a
 * fortnight later, and this does.
 *
 * **What it deliberately does not record, and how that is enforced.** The schema
 * below is the whole schema: a constant route label and the HTTP status of the
 * response. No IP address, no user agent, no country, no referer, no cookie, no
 * derived or hashed identifier of any kind - not "not retained", but never
 * written, so there is nothing to leak, subpoena, or accidentally start querying
 * later. That is a stronger promise than a retention policy and it is checkable:
 * `writeDataPoint` is called in exactly one place, with literal arguments, and
 * `tests/test_site_metrics_counter.py` reads this file and fails if the call
 * grows a field derived from the request.
 *
 * The consequence is that the number is a **request count and not a user count**.
 * Two installs behind one NAT are two, one install polling from two networks is
 * two, and a crawler that fetches the manifest is one more. That is the cost of
 * not identifying anybody, it is the right trade for this project, and the
 * number should be read as a trend rather than as a population.
 *
 * **The counter may never break the endpoint.** `version.json` is a published
 * client contract that a build installed today will still be reading in three
 * years. So the response is produced first and returned unconditionally, the
 * write happens after it and cannot alter it, and every failure inside the write
 * - a missing binding in local dev, a quota refusal, anything unforeseen - is
 * swallowed. A privacy page that is right and an update check that is down is a
 * bad trade.
 *
 * **Why the status is recorded.** It is not identifying and it is the only way
 * to notice the failure this site has actually had: `deploy-site.yml` restores
 * `site/version.json` from the latest release rather than committing it, so a
 * broken restore publishes a site with no manifest. That state is invisible in a
 * request count and obvious in a 404 count.
 */

/** The one path whose requests are counted. Must equal `run_worker_first` in `wrangler.jsonc`. */
const COUNTED_PATH = '/version.json'

/** `blob1` for every data point this Worker writes. A label, not a value from the request. */
const COUNTED_LABEL = 'version-check'

export default {
  /**
   * @param {Request} request
   * @param {{ ASSETS: { fetch: (r: Request) => Promise<Response> }, METRICS?: { writeDataPoint: (p: object) => void } }} env
   */
  async fetch(request, env) {
    // Serving the asset is the job; everything below it is bookkeeping. Assets
    // answers 404 for a path it does not have, which is what we want recorded
    // rather than replaced.
    const response = await env.ASSETS.fetch(request)

    if (new URL(request.url).pathname === COUNTED_PATH) {
      // `env.METRICS` is absent under `wrangler dev --remote=false` and in any
      // deployment that has not been given the binding. Counting nothing is the
      // correct behaviour there; failing is not.
      try {
        env.METRICS?.writeDataPoint({
          blobs: [COUNTED_LABEL],
          doubles: [response.status],
        })
      } catch {
        // Deliberately silent, and deliberately not logged: a log line per
        // failed write on an endpoint every install polls is a way to turn a
        // counter outage into a bill. The absence of data points is itself the
        // signal, and `tools/metrics_snapshot.py` reports a zero rather than
        // hiding it.
      }
    }

    return response
  },
}
