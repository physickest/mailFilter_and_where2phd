export default {
  async email(message, env, ctx) {
    // Best practice: Use env.DESTINATION_EMAIL if set in Cloudflare dashboard
    const DESTINATION_EMAIL = env.DESTINATION_EMAIL || "Your_Email";
    
    // Robust User-Agent to mimic a standard Windows 11 Desktop
    const DESKTOP_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36";

    try {
      const rawEmail = await new Response(message.raw).text();
      const fromAddress = message.from.toLowerCase();

      // 1. Identify Google Scholar Verification Loops
      const scholarRegex = /https:\/\/scholar\.google\.com\/scholar_alerts\?view_op=verify_alert_email[^\s<"]+/;
      const verifyLink = rawEmail.match(scholarRegex);

      if (verifyLink && fromAddress.includes("google.com")) {
        console.log(`[Scholar] Verification link detected: ${verifyLink[0]}`);

        // Using AbortController to implement a 5-second timeout for the fetch
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 5000);

        try {
          const resp = await fetch(verifyLink[0], {
            method: "GET",
            headers: {
              "User-Agent": DESKTOP_UA,
              "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,webp,*/*;q=0.8",
              "Accept-Language": "en-US,en;q=0.5",
              "Referer": "https://scholar.google.com/"
            },
            signal: controller.signal
          });

          clearTimeout(timeout);
          console.log(`[Scholar] Auto-confirm success. HTTP Status: ${resp.status}`);
          
          // If Google returns a challenge (403/429), forward the link to you for manual click
          if (!resp.ok) {
             console.warn(`[Scholar] Google blocked the auto-fetch. Forwarding to user for manual action.`);
             await message.forward(DESTINATION_EMAIL);
          }
        } catch (fetchErr) {
          console.error(`[Scholar] Fetch failed: ${fetchErr.message}. Forwarding email to ensure link isn't lost.`);
          await message.forward(DESTINATION_EMAIL);
        }
        return; 
      }

      // 2. Process Actual Research Alerts
      // Optional: Add semantic filtering here for your PhD research focus (e.g., Robotics, VLA)
      console.log(`[Forwarding] Routing alert from ${fromAddress} to ${DESTINATION_EMAIL}`);
      await message.forward(DESTINATION_EMAIL);

    } catch (e) {
      console.error(`[Critical Error] ${e.stack}`);
      // In case of a total logic crash, try one last attempt to forward the raw message
      try {
        await message.forward(DESTINATION_EMAIL);
      } catch (finalErr) {
        console.error(`[Final Failure] Could not even forward original message: ${finalErr.message}`);
      }
    }
  }
};
