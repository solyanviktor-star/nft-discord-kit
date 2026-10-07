# Support tickets and self-roles

## Tickets

The ticket panel sits in the tickets channel (`python -m kit.setup panels`, or `/nft tickets panel` in
the channel). It has one button per category in `[support] categories` (up to five).

Opening:
1. A member presses a category. If they already have `max_open` open tickets (default 1) they get
   "You already have an open ticket: #thread - write there, or close it first." Staff are not limited.
2. A modal asks "What is it about?" (5 to 1000 characters).
3. The bot opens a **private thread** in the tickets channel named "Category | display name"
   (not invitable; auto-archives after `archive_hours`), and posts:
   - the member's mention and the team: a staff role that is mentionable is mentioned as a role,
     otherwise its members are pinged one by one (the author is not pinged twice);
   - an embed with the question and what the bot knows: NFTs on chain (and specials) with when they
     were read, the member's kit roles, linked wallets and any raffle wallets they set;
   - the **Close ticket** button;
   - the greeting, "**Project:** Thanks! Describe your question below ...".
4. The ticket log channel gets "Ticket #n opened" with links to the member and the thread.

Closing (the **Close ticket** button or `/nft tickets close [note]` inside the thread; staff or the
person who opened it):
1. The whole thread is read into a transcript: a `.txt` file posted to the log channel ("Ticket #n
   closed", who closed it, how many messages, the note), and a JSON copy in `data/tickets/`.
2. "Ticket closed by @x - note. Thanks!" is posted in the thread.
3. The author is removed from the thread (staff keep it and its history), and the thread is archived
   and locked (archived only if the bot lacks Manage Threads).

A thread that was deleted or archived by hand closes its ticket record on the next check, so it does
not count as open.

The bot needs, in the tickets channel: View Channel, Send Messages, Create Private Threads, Send
Messages in Threads, Manage Threads (to lock and remove people), and Mention Everyone if staff roles
are not mentionable. The template's tickets channel grants exactly these. In the log channel: View,
Send, Embed Links, Attach Files.

Members cannot post in the tickets channel itself (read-only for holders), but they can write inside
the private thread they were added to (`allow_threads = true` in the template keeps "Send Messages
in Threads" open).

The default layout shows the tickets channel to holders only. To let anyone ask for help (for
example someone who cannot verify), set that category or channel to `access = "public_readonly"`.

## Self-roles

The self-roles panel (claim-roles channel) has one button per role in `[self_roles] roles` (default:
Giveaway Alerts, the role the raffle cards tag). A press gives the role, a second press takes it away,
with a private confirmation. The roles must sit below the bot's own role; `kit.setup build` creates
them there. Self-roles are not managed by the holder sync.
