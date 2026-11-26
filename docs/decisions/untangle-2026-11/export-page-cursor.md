# A warm restore wipe after a partial SQL read

Open on main d662e63e. The ordinary restore test at fbf3ac15 pins a real multi-page generation and immediately asks the same Owner to wipe. SQLite refuses the table drop and the Owner stays pinned. An independent eviction control on the sandbox wait branch succeeded with the same pin after reopening the Owner.

Two backup readers stop before their SQL statements finish. The base reader returns after crossing the page byte budget while its ordered query still has rows. The change log sizes the next segment and stops before crossing the part budget. Both can leave an outstanding reader on the same connection. Finishing later pages, committing the surrounding synchronous transaction, or issuing another complete query with the same start did not release the first partial base read. An uninterrupted full read allowed the schema change.

The permanent change-log regression at 73625700 uses the existing capture triggers. Two ordinary-sized input writes fit the first segment and the schema change passes. With 375 captured writes to the same input, the first segment ends early and the schema change fails with SQLITE_LOCKED. The setup checks the segment boundary before asking SQLite to drop an unrelated control table.

## Experiments

A disposable base reader selected one row at a time with the existing ordered key, exhausted each statement and kept the existing public cursor. Five Workerd reader controls and the public warm-wipe regression passed. On Node 22 and 24 every page and cursor matched the original for 30,000 small notices, 5,000 wider notices and 60 large input rows. The first wide fixture exceeded the notice schema limit and was replaced with valid 12,000-byte inputs. With prepared statements cached, SQL calls grew from 150 to 30,034, 55 to 5,034 and 6 to 66, respectively. This experiment changes the base reader alone.

A separate five-row Workerd cursor test found that disposing the JavaScript iterator or taking zero more rows still left the table locked. Draining the remaining raw rows allowed the drop. Calling the single-row assertion on multiple remaining rows also released this test's statement by raising an error. The latter is an error path and provides no general cancellation contract.

Next compare a bounded read strategy on both base and change-log readers. Preserve byte-for-byte page and segment boundaries, composite keys, overlay sequence order, retry behavior and peak memory. Include a mixed-width case and measure rows pulled past the byte boundary as well as SQL calls. Restore predicate compatibility and whether a restored waiting turn ever launched are separate open questions.

## A single base part with several change-log segments

The second public restore regression at 5b2406ea updates one 12,000-byte input while capture is active. The saved manifest has exactly one base part and more than one log segment. On main the public wipe stayed pinned with SQLITE_LOCKED. The disposable base-only correction also stayed pinned. Reading both sources in bounded batches and exhausting the last statement let the public wipe complete. The first fixture attempt read a nonexistent root field on the test helper. The committed test reads the actual saved manifest through Files before asserting the shape or requesting the wipe.

The two-reader experiment used 16 rows per SQL statement and discarded the unused tail one row at a time. Five base cursor controls, both public wipes, all 13 Workerd change-log cases (including production sizes) and full Node 22 types passed locally. On Node 22 and 24 the pages and cursors matched the original for all four synthetic data sets. For 30,000 small notices the query count was 1,908 versus 150 on main and 30,034 for one-row reads. For the deliberately mixed-width input stream, eight pages needed 14 statements versus 13 on main, but the reader fetched 60 additional rows containing 722,160 bytes of text. The first mixed fixture exceeded the queue's row limit. The final fixture used 60 valid inputs. A first counting adapter restarted its exhausted native iterator; the corrected adapter kept its exhausted state throughout. The counts above are from the corrected run.

The base and log regressions require a reader-lifetime fix. The bounded-batch proposal is still an experiment. Compare the I/O and memory envelope on production-sized rows and keep the existing export and change-log bounds intact. These tests involve synthetic data in local Workers.
