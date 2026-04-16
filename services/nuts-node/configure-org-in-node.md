
## create a DID in the nuts-node
subject = URA of the organization, in this example we used 00700700
``` bash
curl --location 'localhost:8083/internal/vdr/v2/subject' \
--header 'Content-Type: application/json' \
--data '{"subject": "00700700"}'
```

### Response:
{
   "documents":[
      {
         "@context":[
            "https://www.w3.org/ns/did/v1",
            "https://w3c-ccg.github.io/lds-jws2020/contexts/lds-jws2020-v1.json"
         ],
         "assertionMethod":[
            "did:web:mach2.disyepd.com:nuts-oauth2:iam:6c402131-16f9-4a93-81e9-50af96bf859f#680edfc2-6e8a-4686-b554-f7f6a70ab4d9"
         ],
         "authentication":[
            "did:web:mach2.disyepd.com:nuts-oauth2:iam:6c402131-16f9-4a93-81e9-50af96bf859f#680edfc2-6e8a-4686-b554-f7f6a70ab4d9"
         ],
         "capabilityDelegation":[
            "did:web:mach2.disyepd.com:nuts-oauth2:iam:6c402131-16f9-4a93-81e9-50af96bf859f#680edfc2-6e8a-4686-b554-f7f6a70ab4d9"
         ],
         "capabilityInvocation":[
            "did:web:mach2.disyepd.com:nuts-oauth2:iam:6c402131-16f9-4a93-81e9-50af96bf859f#680edfc2-6e8a-4686-b554-f7f6a70ab4d9"
         ],
         "id":"did:web:mach2.disyepd.com:nuts-oauth2:iam:6c402131-16f9-4a93-81e9-50af96bf859f",
         "verificationMethod":[
            {
               "controller":"did:web:mach2.disyepd.com:nuts-oauth2:iam:6c402131-16f9-4a93-81e9-50af96bf859f",
               "id":"did:web:mach2.disyepd.com:nuts-oauth2:iam:6c402131-16f9-4a93-81e9-50af96bf859f#680edfc2-6e8a-4686-b554-f7f6a70ab4d9",
               "publicKeyJwk":{
                  "crv":"P-256",
                  "kty":"EC",
                  "x":"h7bYOj6c29-eXRG7j6_V7UlTYART1G6G57P-nH_P-Ok",
                  "y":"Zu-rXmOfQHkPVQ-_PFdwQ65h6AcMmrVOxPoxiTeX8Fs"
               },
               "type":"JsonWebKey2020"
            }
         ]
      }
   ],
   "subject":"00700700"
}

## check available DIDs
curl --location 'http://localhost:8083/internal/vdr/v2/subject'
### response:
{
    "00700700":["did:web:mach2.disyepd.com:nuts-oauth2:iam:6c402131-16f9-4a93-81e9-50af96bf859f"]
}

## Create the VC from certificate and nuts-node DID
```bash
CERTS_DIR="$(pwd)/secrets/nuts-node/tls"
MSYS_NO_PATHCONV=1 docker run --rm \
  -v "${CERTS_DIR}:/certs" \
  nutsfoundation/go-didx509-toolkit:1.1.0 \
  vc \
  /certs/mach2.disyepd.com-chain.pem \
  /certs/mach2.disyepd.com.key \
  "CN=Fake UZI Root CA" \
  "did:web:mach2.disyepd.com:nuts-oauth2:iam:6c402131-16f9-4a93-81e9-50af96bf859f"
```
### Copy the response
eyJhbGciOi..........6V2yGdaRw

## Load the VC for the subject (URA)
curl --location 'http://localhost:8083/internal/vcr/v2/holder/00700700/vc' \
--header 'Content-Type: application/json' \
--data '"PASTE THE OUTPUT"'

## test that acces token requests are possible for this organization now
curl --location 'http://localhost:8083/internal/auth/v2/00700700/request-service-access-token' \
--header 'Content-Type: application/json' \
--data '{
  "authorization_server": "https://mach2.disyepd.com/nuts-oauth2/oauth2/00700700",
  "scope": "eOverdracht-receiver"
}'
