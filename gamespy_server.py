from twisted.internet.protocol import Factory
from twisted.internet.endpoints import serverFromString
from twisted.protocols.basic import LineReceiver
from twisted.internet import reactor

import gamespy.gs_database as gs_database
import gamespy.gs_query as gs_query
import gamespy.gs_utility as gs_utils
import other.utils as utils

class PlayerSession(LineReceiver):
    def __init__(self, sessions, addr):
        self.sessions = sessions
        self.leftover = ""
        self.setRawMode() # We're dealing with binary data so set to raw mode
        self.db = gs_database.GamespyDatabase()
        self.profileId = 0
        self.address = addr
        self.gameid = ""

    def get_ip_as_int(self, address):
        ipaddress = 0

        if address != None:
            for n in address.split('.'):
                ipaddress = (ipaddress << 8) | int(n)

        return ipaddress

    def connectionMade(self):
        # Create new session id
        self.session = ""
        self.challenge = utils.generate_random_str(8)

        msg_d = []
        msg_d.append(('__cmd__', "lc"))
        msg_d.append(('__cmd_val__', "1"))
        msg_d.append(('challenge', self.challenge))
        msg_d.append(('id', "1"))
        msg = gs_query.create_gamespy_message(msg_d)

        utils.print_log("SENDING: '%s'..." % msg)
        self.transport.write(bytes(msg))

    def connectionLost(self, reason):
        if self.session in self.sessions:
            del self.sessions[self.session]

    def rawDataReceived(self, data):
        utils.print_log("RESPONSE: %s" % data)

        data = self.leftover + data
        commands, self.leftover = gs_query.parse_gamespy_message(data)

        for data_parsed in commands:
            print data_parsed

            if data_parsed['__cmd__'] == "login":
                self.perform_login(data_parsed)
            elif data_parsed['__cmd__'] == "logout":
                self.perform_logout(data_parsed)
            elif data_parsed['__cmd__'] == "getprofile":
                self.perform_getprofile(data_parsed)
            elif data_parsed['__cmd__'] == "updatepro":
                self.perform_updatepro(data_parsed)
            elif data_parsed['__cmd__'] == "ka":
                self.perform_ka(data_parsed)
            elif data_parsed['__cmd__'] == "status":
                self.perform_status(data_parsed)
            elif data_parsed['__cmd__'] == "bm":
                self.perform_bm(data_parsed)
            elif data_parsed['__cmd__'] == "addbuddy":
                self.perform_addbuddy(data_parsed)
            elif data_parsed['__cmd__'] == "authadd":
                self.perform_authadd(data_parsed)
            else:
                # Maybe write unknown commands to a separate file later so new data can be collected more easily?
                utils.print_log("Found unknown command, don't know how to handle '%s'." % data_parsed['__cmd__'])

    def perform_login(self, data_parsed):
        authtoken_parsed = gs_utils.parse_authtoken(data_parsed['authtoken'])
        print authtoken_parsed

        # Track what console is connecting and save it in the database during user creation just in case we can use
        # the information in the future.
        console = 0 # 0 = NDS, 1 = Wii

        # get correct information
        userid = authtoken_parsed['userid']

        # The Wii does not use passwd, so take another uniquely generated string as the password.
        if "passwd" in authtoken_parsed:
            password = authtoken_parsed['passwd']
        else:
            password = authtoken_parsed['gsbrcd']
            console = 1

        gsbrcd = authtoken_parsed['gsbrcd']
        uniquenick = utils.base32_encode(int(userid)) + gsbrcd
        email = uniquenick + "@nds" # The Wii also seems to use @nds.

        # Wii: Serial number
        if "csnum" in authtoken_parsed:
            csnum = authtoken_parsed['csnum']
            console = 1
        else:
            csnum = ""

        # Wii: Friend code
        if "cfc" in authtoken_parsed:
            cfc = authtoken_parsed['cfc']
            console = 1
        else:
            cfc = ""

        # NDS: Wifi network's BSSID
        if "bssid" in authtoken_parsed:
            bssid = authtoken_parsed['bssid']
        else:
            bssid = ""

        # NDS: Device name
        if "devname" in authtoken_parsed:
            devname = authtoken_parsed['devname']
        else:
            devname = ""

        # NDS: User's birthday
        if "birth" in authtoken_parsed:
            birth = authtoken_parsed['birth']
        else:
            birth = ""

        # Verify the client's response
        valid_response = gs_utils.generate_response(self.challenge, authtoken_parsed['challenge'], data_parsed['challenge'], data_parsed['authtoken'])
        if data_parsed['response'] != valid_response:
            utils.print_log("ERROR: Got invalid response. Got %s, expected %s" % (data_parsed['response'], valid_response))

        proof = gs_utils.generate_proof(self.challenge, authtoken_parsed['challenge'], data_parsed['challenge'], data_parsed['authtoken'])

        valid_user = self.db.check_user_exists(userid, gsbrcd)
        if valid_user == False:
            profileid = self.db.create_user(userid, password, email, uniquenick, gsbrcd, console, csnum, cfc, bssid, devname, birth)
        else:
            profileid = self.db.perform_login(userid, password, gsbrcd)

            if profileid == None:
                 # Handle case where the user is invalid
                print "Invalid password"

        if profileid != None:
            # Successfully logged in or created account, continue creating session.
            sesskey = self.db.create_session(profileid)

            self.sessions[profileid] = self

            msg_d = []
            msg_d.append(('__cmd__', "lc"))
            msg_d.append(('__cmd_val__', "2"))
            msg_d.append(('sesskey', sesskey))
            msg_d.append(('proof', proof))
            msg_d.append(('userid', userid))
            msg_d.append(('profileid', profileid))
            msg_d.append(('uniquenick', uniquenick))
            msg_d.append(('lt', gs_utils.base64_encode(utils.generate_random_str(16)))) # Some kind of token... don't know it gets used or generated, but it doesn't seem to have any negative effects if it's not properly generated.
            msg_d.append(('id', data_parsed['id']))
            msg = gs_query.create_gamespy_message(msg_d)

            # Take the first 4 letters of gsbrcd instead of gamecd because they should be consistent across game
            # regions. For example, the US version of Metroid Prime Hunters has the gamecd "AMHE" and the first 4 letters
            # of gsbrcd are "AMHE". However, the Japanese version of Metroid Prime Hunters has the gamecd "AMHJ" with
            # the first 4 letters of bsbrcd as "AMHE". Tetris DS is the other way, with the first 4 letters as the
            # Japanese version (ATRJ) while the gamecd is region specific (ATRE for US and ATRJ for JP).
            # gameid is used to send all people on the player's friends list a status updates, so don't make it region
            # specific.
            self.gameid = gsbrcd[0:4]
            self.profileid = profileid

            utils.print_log("SENDING: %s" % msg)
            self.transport.write(bytes(msg))

            # Send any friend statuses when the user logs in.
            # This will allow the user to see if their friends are hosting a game as soon as they log in.
            self.get_status_from_friends()

    def perform_logout(self, data_parsed):
        print "Session %s has logged off" % (data_parsed['sesskey'])
        self.db.delete_session(data_parsed['sesskey'])

    def perform_getprofile(self, data_parsed):
        #profile = self.db.get_profile_from_session_key(data_parsed['sesskey'])
        profile = self.db.get_profile_from_profileid(data_parsed['profileid'])

        # Wii example: \pi\\profileid\474888031\nick\5pde5vhn1WR9E2g1t533\userid\442778352\email\5pde5vhn1WR9E2g1t533@nds\sig\b126556e5ee62d4da9629dfad0f6b2a8\uniquenick\5pde5vhn1WR9E2g1t533\pid\11\lon\0.000000\lat\0.000000\loc\\id\2\final\
        msg_d = []
        msg_d.append(('__cmd__', "pi"))
        msg_d.append(('__cmd_val__', ""))
        msg_d.append(('profileid', profile['profileid']))
        msg_d.append(('nick', profile['uniquenick']))
        msg_d.append(('userid', profile['userid']))
        msg_d.append(('email', profile['email']))
        msg_d.append(('sig', utils.generate_random_hex_str(32)))
        msg_d.append(('uniquenick', profile['uniquenick']))
        msg_d.append(('pid', profile['pid']))

        if profile['firstname'] != "":
            msg_d.append(('firstname', profile['firstname'])) # Wii gets a firstname

        msg_d.append(('lastname', profile['lastname']))
        msg_d.append(('lon', profile['lon']))
        msg_d.append(('lat', profile['lat']))
        msg_d.append(('loc', profile['loc']))
        msg_d.append(('id', data_parsed['id']))
        msg = gs_query.create_gamespy_message(msg_d)

        utils.print_log("SENDING: %s" % msg)
        self.transport.write(bytes(msg))


    def perform_updatepro(self, data_parsed):
        # Wii example: \updatepro\\sesskey\199714190\firstname\Wii:2555151656076614@WR9E\partnerid\11\final\
        sesskey = data_parsed['sesskey']

        # Remove any fields not related to what we should be updating.
        # To avoid any crashes, make sure the key is actually in the dictionary before removing it.
        if "__cmd__" in data_parsed:
            data_parsed.pop('__cmd__')
        if "__cmd_val__" in data_parsed:
            data_parsed.pop('__cmd_val__')
        if "updatepro" in data_parsed:
            data_parsed.pop('updatepro')
        if "partnerid" in data_parsed:
            data_parsed.pop('partnerid')
        if "sesskey" in data_parsed:
            data_parsed.pop('sesskey')

        # Create a list of fields to be updated.
        fields = []
        for f in data_parsed:
            fields.append((f, data_parsed[f]))

        self.db.update_profile(sesskey, fields)

    def perform_ka(self, data_parsed):
        # No op
        return

    def perform_status(self, data_parsed):
        sesskey = data_parsed['sesskey']

        #fields = []
        #fields.append(("stat", data_parsed['statstring']))
        #fields.append(("loc", data_parsed['locstring']))

        #self.db.update_profile(sesskey, fields)

        self.status = data_parsed['__cmd_val__']
        self.statstring =  data_parsed['statstring']
        self.locstring =  data_parsed['locstring']

        self.send_status_to_friends()

    def perform_bm(self, data_parsed):
        if data_parsed['__cmd_val__'] == "1": # Message to/from clients?
            if "t" in data_parsed:
                # Send message to the profile id in "t"
                dest_profileid = int(data_parsed['t'])
                dest_msg = data_parsed['msg']

                msg_d = []
                msg_d.append(('__cmd__', "bm"))
                msg_d.append(('__cmd_val__', "1"))
                msg_d.append(('f', self.profileid))
                msg_d.append(('msg', dest_msg))
                msg = gs_query.create_gamespy_message(msg_d)

                utils.print_log("SENDING TO %s:%s: %s" % (self.sessions[dest_profileid].address.host, self.sessions[dest_profileid].address.port, msg))
                self.sessions[dest_profileid].transport.write(bytes(msg))


    def perform_addbuddy(self, data_parsed):
        # Sample: \addbuddy\\sesskey\231601763\newprofileid\476756820\reason\\final\
        buddies = self.db.get_buddy_list(self.profileid)

        buddy_exists = False
        for buddy in buddies:
            if buddy['buddyProfileId'] == data_parsed['newprofileid']:
                buddy_exists = True
                break

        if not buddy_exists:
            self.db.add_buddy(self.profileid, data_parsed['newprofileid'])

        # In the case that the user is already a buddy:
        # \bm\2\f\217936895\msg\|signed|f259f26d3273f8bda23c7c5e4bd8c5aa\final\
        # \error\\err\1539\errmsg\The profile requested is already a buddy.\final\
        # Handle later?

    def perform_authadd(self, data_parsed):
        # Sample: \authadd\\sesskey\231587549\fromprofileid\217936895\sig\f259f26d3273f8bda23c7c5e4bd8c5aa\final\
        self.db.auth_buddy(self.profileid, data_parsed['newprofileid'])

        # After authorization, send the person who was authorized a message like so:
        # \bm\1\f\476756820\msg\I have authorized your request to add me to your list\final\

    def send_status_to_friends(self):
        # TODO: Cache buddy list so we don't have to query the database every time
        buddies = self.db.get_buddy_list(self.profileid)

        status_msg = "|s|%s|ss|%s|ls|%s|ip|%d|p|0|qm|0" % (self.status, self.statstring, self.locstring, self.get_ip_as_int(self.address.host))

        msg_d = []
        msg_d.append(('__cmd__', "bm"))
        msg_d.append(('__cmd_val__', "100"))
        msg_d.append(('f', self.profileid))
        msg_d.append(('msg', status_msg))
        msg = gs_query.create_gamespy_message(msg_d)

        for buddy in buddies:
            if buddy['buddyProfileId'] in self.sessions:
                self.sessions[buddy['buddyProfileId']].transport.write(bytes(msg))

    def get_status_from_friends(self):
        # This will be called when the player logs in. Grab the player's buddy list and check the current sessions to
        # see if anyone is online. If they are online, make them send an update to the calling client.
        buddies = self.db.get_buddy_list(self.profileid)

        for buddy in buddies:
            if buddy['buddyProfileId'] in self.sessions and self.sessions[buddy['buddyProfileId']].gameid == self.gameid:
                status_msg = "|s|%s|ss|%s|ls|%s|ip|%d|p|0|qm|0" % (self.sessions[buddy['buddyProfileId']].status, self.sessions[buddy['buddyProfileId']].statstring, self.sessions[buddy['buddyProfileId']].locstring, self.get_ip_as_int(self.sessions[buddy['buddyProfileId']].address.host))

                msg_d = []
                msg_d.append(('__cmd__', "bm"))
                msg_d.append(('__cmd_val__', "100"))
                msg_d.append(('f', buddy['buddyProfileId']))
                msg_d.append(('msg', status_msg))
                msg = gs_query.create_gamespy_message(msg_d)

                self.transport.write(bytes(msg))


class PlayerSearch(LineReceiver):
    def __init__(self, sessions, addr):
        self.sessions = sessions
        self.setRawMode()
        self.db = gs_database.GamespyDatabase()
        self.leftover = ""

    def connectionMade(self):
        pass

    def connectionLost(self, reason):
        pass

    def rawDataReceived(self, data):
        utils.print_log("SEARCH RESPONSE: %s" % data)

        data = self.leftover + data
        commands, self.leftover = gs_query.parse_gamespy_message(data)

        for data_parsed in commands:
            print data_parsed

            if data_parsed['__cmd__'] == "otherslist":
                self.perform_otherslist(data_parsed)
            else:
                utils.print_log("Found unknown search command, don't know how to handle '%s'." % data_parsed['__cmd__'])

    def perform_otherslist(self, data_parsed):
        # Reference: http://wiki.tockdom.com/wiki/MKWii_Network_Protocol/Server/gpsp.gs.nintendowifi.net
        # Example from: filtered-mkw-log-2014-01-01-ct1310.eth
        # \otherslist\\o\146376154\uniquenick\2m0isbjmvRMCJ2i5321j\o\192817284\uniquenick\1jhggtmghRMCJ2jrsh23\o\302594991\uniquenick\7dkjp51v5RMCJ2nr3vs9\o\368031897\uniquenick\1v7p3qmkpRMCJ1o8f56p\o\447214276\uniquenick\7dkt0p6gtRMCJ2ljh72h\o\449615791\uniquenick\4puvrm1g4RMCJ00ho3v1\o\460250854\uniquenick\4rik5l1u1RMCJ0tc3fii\o\456284963\uniquenick\1unitvi86RMCJ1b10u02\o\453830866\uniquenick\7de3q52dbRMCJ2877ss2\o\450197498\uniquenick\3qtutr1ikRMCJ38gem1n\o\444241868\uniquenick\67tp53bs9RMCJ1abs7ej\o\420030955\uniquenick\5blesqia3RMCJ322bbd6\o\394609454\uniquenick\0hddp7mq2RMCJ30uv7r7\o\369478991\uniquenick\59de9c2bhRMCJ0re0fii\o\362755626\uniquenick\5tte2lif7RMCJ0cscgtg\o\350951571\uniquenick\7aeummjlaRMCJ3li4ls2\o\350740680\uniquenick\484uiqhr4RMCJ18opoj0\o\349855648\uniquenick\5blesqia3RMCJ1c245dn\o\324078642\uniquenick\62go5gpt0RMCJ0v0uhc9\o\304111337\uniquenick\4lcg6ampvRMCJ1gjre51\o\301273266\uniquenick\1dhdpjhn8RMCJ2da6f9h\o\193178453\uniquenick\3pcgu0299RMCJ3nhu50f\o\187210028\uniquenick\3tau15a9lRMCJ2ar247h\o\461622261\uniquenick\59epddrnkRMCJ1t2ge7l\oldone\\final\

        msg_d = []
        msg_d.append(('__cmd__', "otherslist"))
        msg_d.append(('__cmd_val__', ""))

        if "numopids" in data_parsed and "opids" in data_parsed:
            numopids = int(data_parsed['numopids'])
            opids = data_parsed['opids'].split('|')

            if len(opids) != numopids:
                print "Unexpected number of opids, got %d, expected %d." % (len(opids), numopids)

            # Return all uniquenicks despite any unexpected/missing opids
            for opid in opids:
                profile = self.db.get_profile_from_profileid(opid)

                if profile != None:
                    msg_d.append(('o', opid))
                    msg_d.append(('uniquenick', profile['uniquenick']))

        msg_d.append(('oldone', ""))
        msg = gs_query.create_gamespy_message(msg_d)

        self.transport.write(bytes(msg))


sessions = {}
class PlayerFactory(Factory):
    def __init__(self):
        # Instead of storing the sessions in the database, it might make more sense to store them in the PlayerFactory.
        print "Now listening for connections..."

    def buildProtocol(self, addr):
        return PlayerSession(sessions, addr)


class PlayerSearchFactory(Factory):
    def __init__(self):
        self.sessions = {}
        print "Now listening for player search connections..."

    def buildProtocol(self, addr):
        return PlayerSearch(sessions, addr)


endpoint = serverFromString(reactor, "tcp:29900")
conn = endpoint.listen(PlayerFactory())

endpoint_search = serverFromString(reactor, "tcp:29901")
conn_search = endpoint.listen(PlayerSearchFactory())

reactor.run()

