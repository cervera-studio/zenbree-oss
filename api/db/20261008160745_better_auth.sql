-- #348: the time zone the user chose in the profile; null follows the browser's (a Better Auth additional field).
alter table "user" add column "timeZone" text check ("timeZone" is null or char_length("timeZone") <= 64);
