"""Well-known passwords of policy length (12+ characters), compared case-insensitively.

A small local list of the most common long passwords and patterns from public
breach corpora. It is a first line of defence only: it is not a complete
breached-password check (see PROJECT_STATUS.md, security limitations).
"""

_PASSWORDS = """
123456789012 1234567890123 12345678901234 123456789123 1234567891011 123123123123 111111111111
000000000000 121212121212 123412341234 987654321098 098765432109 147258369147 123456123456
qwertyuiopas qwertyuiop12 qwertyuiop123 qwerty123456 qwertyqwerty 1qaz2wsx3edc 1q2w3e4r5t6y
1q2w3e4r5t6y7u asdfghjkl123 asdfghjklzxc zxcvbnm12345 qazwsxedcrfv q1w2e3r4t5y6 abc123abc123
abcdefghijkl abcd1234abcd password1234 password12345 password123456 passwordpassword password1!
password@123 password!123 p@ssw0rd1234 p@ssword1234 passw0rd1234 mypassword123 newpassword123
changeme1234 changemenow! letmein12345 letmeinplease welcome12345 welcome@1234 welcome123456
administrator admin1234567 adminadmin12 admin@123456 administrator1 iloveyou1234 iloveyou12345
sunshine1234 football1234 baseball1234 superman1234 batman123456 princess1234 starwars1234
dragon123456 monkey123456 trustno1trustno1 opspilot1234 opspilotadmin opspilot@123 companyname1
correcthorsebatterystaple thequickbrownfox loremipsumdolor 1234567890qwerty qwerty1234567890
helloworld123 hello1234567 summer2024!! summer202412 winter202412 spring202412 autumn202412
january12345 december1234 secret123456 secretpassword computer1234 internet1234 testtest1234
test12345678 testpassword user12345678 username1234 default12345 guest1234567 master123456
"""

COMMON_PASSWORDS: frozenset[str] = frozenset(word.casefold() for word in _PASSWORDS.split())
