# Forkcast — Menu Matcher

*Your food forecast before you eat out.*

Forkcast is a single-page web app. You pick your dietary preferences, paste a link to a restaurant's menu and get back a list of dishes rated **Clear**, **Ask first** or **Avoid**, along with the restaurant's location when the website lists it. You can download the results as a one-page PDF.

The front end is plain HTML, CSS and JavaScript, with no build step. An **n8n** workflow does the menu analysis with AI.

## Project files

| File | Purpose |
| --- | --- |
| `index.html` | Page markup: logo intro, form, loading modal, results modal |
| `style.css` | Monochrome "Classical" design tokens, component styles and the PDF layout |
| `script.js` | Form state, the n8n request, turning the reply into dishes, modals, PDF export |
| `n8n_flow.json` | The n8n workflow (webhook → read menu page → AI agent → formatted reply), ready to import |

## Setting up the n8n flow

The workflow in `n8n_flow.json` works like this:

1. **Webhook:** receives the form.
2. **Parse Message:** reads the preferences and the menu link.
3. **HTTP Request:** fetches the menu page as text through [Jina Reader](https://jina.ai/reader/).
4. **AI Agent** (Google Gemini): rates each dish and finds the restaurant's name and address.
5. **Format Reply:** splits off the name and location.
6. **Respond to Webhook:** sends `{ message, restaurantName, location }` back to the page.

### 1. Install and start n8n

You need [Node.js](https://nodejs.org/) 20 or newer.

```bash
npm install -g n8n
n8n start
```

Or run it without installing: `npx n8n`.

Wait until the terminal shows `Editor is now accessible via: http://localhost:5678`, then open that address and create your local owner account.

> On Windows, clicking inside the terminal window can pause n8n while it starts. If it seems stuck, click into the terminal and press **Enter**.

### 2. Import the workflow

1. In n8n, go to **Workflows → Create workflow**.
2. Open the **⋯** menu (top right), choose **Import from File…** and pick `n8n_flow.json`.
3. Save the workflow.

### 3. Add your Gemini API key

1. Get a free API key from [Google AI Studio](https://aistudio.google.com/app/apikey).
2. In the workflow, open the **Google Gemini Chat Model** node.
3. Under **Credential**, choose **Create new credential** and paste the key.
4. Save. The exported file only holds the credential's name, never the key, so everyone who imports it needs to add their own.

The Jina Reader step needs no key for light use. If you hit rate limits, add an `Authorization: Bearer <your Jina key>` header to the **HTTP Request** node.

### 4. Turn on the webhook

- **For everyday use:** activate the workflow (the **Active** toggle, or **Publish** in newer n8n versions). The page then uses `http://localhost:5678/webhook/get-menu-results`.
- **For testing in the editor:** click **Execute workflow / Listen for test event** and change `N8N_WEBHOOK_URL` in `script.js` to `http://localhost:5678/webhook-test/get-menu-results`. Each click accepts one request.

CORS is already allowed for any origin in the Webhook node (`allowedOrigins: *`). That's fine locally, but restrict it to your site's address if you ever host the workflow publicly.

### 5. Open the front end

Serve the folder and open it in a browser, for example:

```bash
python -m http.server 8000     # then open http://localhost:8000
```

`npx serve .` or the VS Code Live Server extension also work. Opening `index.html` straight from disk works too.

The page needs an internet connection for Google Fonts (Cormorant Garamond and Lora) and for `html2pdf.js`, which comes from cdnjs.

## The n8n connection

The request is the same as before the redesign. The front end sends a `POST` with `multipart/form-data` to the webhook:

| Field | Value |
| --- | --- |
| `preferences` | Comma-separated, e.g. `gluten intolerance, vegan` |
| `menuUrl` | The menu link (empty string if none) |
| `menu` | The PDF file. Not sent at the moment, because the PDF upload is hidden (see [Future enhancements](#future-enhancements)). |

The n8n webhook must allow CORS from wherever the page is served.

### What the reply can look like

Any of these work:

- Plain text or markdown
- JSON such as `{ "output": "..." }`; `text`, `response` or `message` also work
- An array whose first item is one of the above

Optional JSON fields:

- `restaurantName` (or `restaurant` / `name`): shown in the results and the PDF. Without it, the name comes from the URL hostname.
- `location` (or `restaurantLocation` / `address`): shown with a pin under the results title and in the PDF header. If it's missing, the results popup leaves the line out and the PDF says "Location not provided". The AI agent reads the address from the restaurant's page and never guesses one.
- `dishes`: `[{ "name": "...", "verdict": "clear" | "ask" | "avoid", "note": "..." }]`. If this is present, the front end uses it directly and skips the text parsing.

### How dishes are read from a text reply

The AI reply is grouped into sections. The section heading decides the verdict, and each bullet under it becomes a dish:

```
*🟢 Naturally Gluten-Free*
- *Edamame* – steamed with sea salt

*🟡 Ask for changes*
- **Shiitake Ramen** - ask for rice noodles

*🔴 Avoid*
- Vegetable Gyoza – wheat wrappers
```

- Headings can be `*bold*`, `**bold**`, `## markdown`, an emoji line or a line ending in `:`.
- Headings with 🟢 / ✅ / "safe" / "suitable" / "naturally" / "free" count as **Clear**.
- Headings with 🟡 / 🟠 / ⚠ / "ask" / "modify" / "swap" / "request" / "check" count as **Ask first**.
- Headings with 🔴 / ❌ / "avoid" / "not" / "contains" count as **Avoid**.
- A bullet can also have its own emoji, which overrides its section.
- Each bullet is split into a name and a note at the first ` – `, ` - `, `: ` or `( … )`.

If no dishes can be found, the results modal shows the whole reply as formatted text instead.

## Design notes

- Monochrome palette (`#111111` on `#FFFFFF`, greys for dividers and tags). Cormorant Garamond for headings, Lora for body text.
- The **Avoid** tag has an × glyph and the dish name is greyed out, so it is easy to tell apart from **Clear** in black and white.
- The loading modal moves through 5 steps. If n8n is still working after step 5, it keeps rotating extra messages so it never looks stuck. When the reply arrives, the bar fills to 100%.
- Errors (n8n unreachable, a non-200 response, an invalid URL) appear inline under the menu section.
- Closing the results ("×", **Try another menu**, Esc or a click on the backdrop) keeps your inputs, so you can change them and try again.

## Future enhancements

- **Menu PDF upload.** The upload field is already built but hidden in `index.html`: the `<div class="pdf-upload" hidden>` block. The frontend sends the file as `menu`. To turn it on:
  1. In n8n, add an **IF** node after Parse Message that checks for an uploaded file.
  2. On the "file uploaded" branch, add an **Extract From File** node (PDF → text) that feeds the AI Agent. The link branch keeps using the Jina HTTP Request.
  3. Let Parse Message accept a request with a PDF but no link.
  4. Remove the `hidden` attribute from the `pdf-upload` block.
- **Better error messages.** At the moment errors show as one line of text under the menu field, and sometimes it's the raw technical message, e.g. `n8n responded with 500: {...}`. Improvements:
  - Friendly messages for each case: n8n not running, the restaurant page couldn't be read, the AI took too long, or no menu was found on the page.
  - A proper error state, either a styled alert or an error version of the results popup, with a **Try again** button.
  - Have n8n return errors as JSON (e.g. `{ "error": "page_unreadable" }`) by using the nodes' error outputs and a Respond to Webhook node, instead of failing with a generic 500.
- **A backup location lookup.** When the restaurant's page doesn't list an address, look it up with the Google Places API (Text Search) using the restaurant name and website.

## Disclaimer

The suggestions are generated by AI as part of a fun project and may not be accurate. Always confirm ingredients and allergens with the restaurant, especially if you have an allergy or coeliac disease.
