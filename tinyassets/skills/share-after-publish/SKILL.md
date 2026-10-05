---
name: share-after-publish
description: Offer to share a successful publication or update with a short post and its public preview picture.
---

After a publish or update succeeds, read its completion receipt (on an approved
request, `answer.completion` in `read_graph target="pending_requests"`). Check
the listing, change kind and version. An approval alone is not publication success.

Look at the user's connected platforms and their saved connection skills. Offer
to post on suitable ones, suggest a short post about what became public or changed,
and include the picture at `preview_image_path` when its status is `ready`.
Use `share_url` if supplied; never invent a listing link. If the picture is
unavailable, say so and offer the draft without pretending to have one. Use only
public details in the draft and picture, never private conversations or live data.

For example: "Your update is published. Would you like to share it on a connected
platform? Suggested post: 'I just updated my command center: [public change].'
I can include the preview picture."

Never post without the user's explicit approval of the destination, text and
picture. Then use the normal approval sheet and owner rules for that connection;
publishing approval is not posting approval. If nothing suitable is connected,
mention that they can connect a platform, using the connect skill if they want.

This is your starter recipe. You can edit or delete it.
