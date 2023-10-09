## MIDI Hub

## Introduction

So you want to collaborate (musically) with someone on the other side of the internet. Turns out there is [a protocol for that](https://www.rfc-editor.org/rfc/rfc4695) with implementations for Mac (natively), [Windows](https://www.tobias-erichsen.de/software/rtpmidi.html) and [Linux](https://github.com/davidmoreno/rtpmidid). In theory, you spin up the RTP MIDI implementation of your choice and away you go.

But there will be bumps along the way if you intend on hosting your Internet-based MIDI host on a virtual machine. The first is that a sound card is required to make this work (more on this in a moment) - not an easy ask for a virtual machine. The second is that you'll need some sort of MIDI mixer/sequencer (that probably isn't the right term but go with me here) that allows you to connect MIDI sources and targets together. With a GUI there are a few options here ([Ableton](https://www.ableton.com/en/) is a good example for Mac) but your virtual machine may not have a GUI.

This (somewhat tiny!) repo is here to help. Especially if you want to do this with Linux. And it's v2 because this is slightly different to my original repo because it uses a different RTP MIDI library to communicate.

Linux can solve the first problem (virtual sound card) by supplying a "dummy" sound card, even in a virtual server environment. Without this, most applications that use the [ALSA](https://wiki.archlinux.org/title/Advanced_Linux_Sound_Architecture) libraries won't work. That includes anything that wants to use MIDI. Even if you have the ALSA tools and libraries installed, if there isn't a sound card present then everything just fails.

The second problem is solved using ALSA as well - there is a command-line tool called `aconnect` which allows you to join those MIDI sources and targets with each other.

The final piece of the puzzle is the different [RTP MIDI handler from McLaren Labs](https://mclarenlabs.com/rtpmidi/). Note that this is software you must purchase - it is not included in this repo. The instructions below will tell you how to install it but you must buy it from them.

In this repo this is what you get:

 - midihub.py - Python script that launches `rtpmidi` and when the listeners are running it joins them together in a specific way - more details below.
 - midihub-cloudformation.yml - [AWS CloudFormation](https://aws.amazon.com/cloudformation/) template for building an appropriate Linux instance and deploying into AWS. More details on that below.
 - lambda-midiHubStats.py - Code for a Lambda function which are automatically deployed by the CloudFormation template to respond to request when asked for latency information. If you're not deploying this using CloudFormation you can use this code to query the database.
 - latency.html - Source HTML file for a (very simple!) web front end to calls Lambda latency function via API Gateway. Feel free to modify these or embed the code into your own web page. Designed to show who is connected and what their round-trip latency is. These are modified during setup with the appropriate API Gateway endpoint.
 - lambda-getTransmitPorts.py - Code for a Lambda function that retrieves the "transmit" MIDI ports from DynamoDB and sends them back to the caller.
 - lambda-resetStuckNote.py - Receives calls from the HTML file below and sends requests to a SQS queue to reset "stuck" notes.
 - fixstucknotes.html - Source HTML file for (another simple) web front end that first determines the ports in use and second can call the other Lambda function to send MIDI messages to reset "stuck" notes in the MIDI stream.
 - fix-stuck-notes.py - This runs on the instance and receives SQS messages from the Lambda function above. When it receives a port number and note "range" it sends NoteOff messages to the port to clear any "stuck" notes.
 - update-latency.py - A script that runs on the instance. It trawls the log files from `rtpmidi` and sends the contents to a DynamoDB database. Scheduled to run via cron once every minute.
 - create-s3-bucket.py - After the instance has been created this runs to create a S3 bucket with a unique name; link the CloudFront distirbution to it; set up secure access (the S3 bucket is not public; only CloudFront can access it); and uploads the HTML file after modifying it with the API Gateway endpoint URL. Note that if you are not deploying in the `us-east-1` region it make take some time (hours) for the CloudFront/S3 pair to work correctly.
 - midi-monitor.py - A troubleshooting tool to see what is being received on specific also ports. Find the name of the existing ports by running `aconnect -l` then use the port name (e.g. 'midiHub-GroupOne-5040') as a parameter to this utility. It will display notes currently playing the the MIDI channels they are playing on. Press ^C to exit.
 - alsaserver.py - A workaround for a small issue - this is used for "sanitising" the MIDI input.

The intention is that you can run this solution when you need it and shut it down when you don't. To shut the solution down, you can go into the [EC2 console](https://console.aws.amazon.com/ec2/), select the instance labelled `midiHubv2` then choose "Instance state" (top-right of the browser window) and click "Stop instance". You'll notice there is a "Start instance" choice there too - that's how you can restart the virtual machine running MidiHub.

If you choose "Terminate instance" then everything will be deleted - you'll have to deploy it again (see the instructions in the next section). Terminating the instance will mean that you are not paying anything while you are not using it. When it is in a "stopped" state you will be charge a little for the persitent storage - about US$0.10 (that's ten cents) per month.

EC2 instance pricing [can be found here](https://aws.amazon.com/ec2/pricing/on-demand/) - for most purposes the t3 instances will be fine - other instance types offer higher CPU speeds and better networking performance. MIDI is pretty low network usage so those should not be required. Other costs will be [API Gateway](https://aws.amazon.com/api-gateway/pricing/), [Lambda](https://aws.amazon.com/lambda/pricing/), [S3](https://aws.amazon.com/s3/pricing/) and [Global Accelerator](https://aws.amazon.com/global-accelerator/pricing/). Out of all of these, EC2 and Global Accelerator will be the majority of the AWS charges.

## Deploy the CloudFormation template

To deploy in AWS, go to the [CloudFromation console](https://console.aws.amazon.com/cloudformation/) and make sure you're deploying in the right AWS region. Typically you want to choose the region which is the lowest latency (over the internet) between all of the participants and that region. Then choose "Create stack" and when asked, upload the template file from this repo.

You'll be asked for the instance type 

The template assumes that your account has a default VPC which hasn't been modified - it will have a default public subnet with an Internet Gateway. Most accounts will have this (it is the default after all) but if you don't you'll need to modify the template to use a specific VPC.

The template creates:

 - an EC2 instance
 - an EC2 instance profile and an IAM role
 - a Security Group
 - an Elastic IP
 - a Global Acceelerator linked to the Elastic IP
 - a dummy CloudFront distribution that gets modified by the `create-s3-bucket.py` script
 - an API Gateway with three routes to...
 - ...three Lambda functionss
 - a DynamoDB database
 - a SQS queue
 - and a bunch of glue to hold all of these things together.

Deployment takes around ten minutes - there are a bunch of packages to install. Note that when the CloudFormation service says that deployment is complete, you will need to wait for the rest of the tasks on the instance (such as the compilation) to complete.

Note that the name of the CloudFormation stack that you deploy should be unique if you are going to deploy MidiHub in multiple regions. For example "MidiHub-Sydney" and another "MidiHub-Singapore". This will prevent global resource name conflicts.

Once complete you will need to download rtpmidi from McLaren labs and install it using `sudo dpkg -i rtpmidi_1.1.2-ubuntu22.04_amd64.deb` - the default installation directory is fine but if you move it, edit `midihub.py` to reflect the new location.

If you encounter errors then perform the following steps:
```
sudo apt-get --fix-broken install -y
sudo dpkg -i rtpmidi_1.1.2-ubuntu22.04_amd64.deb
```

Outputs from the CloudFormation template of interest are:

 - The Elastic IP that you should use to connect to midiHub.
 - The two IP addresses for Global Accelerator (more on that below).
 - The API Gateway base URL. On that endpoint you'll find /latency, /getTransmitPorts and /resetStuckNote. Examples of how to use these are in the HTML files.
 - The two CloudFront-hosted HTML URLs (latency.html and fixstucknotes.html).
 - SQS Queue URL which is used by other code in the system.
 - DynamoDB table name which is used by other code in the system.

The Elastic IP may result in charges to your account. If you are shutting down the MidiHub instance to save costs (this is a good idea!) you will be charged for the Elastic IP because it is unused. On [the pricing page](https://aws.amazon.com/ec2/pricing/on-demand/#Elastic_IP_Addresses) you can see that this will result in an extra charge of around US$4 per month. You can delete the entire CloudFormation stack (which will eliminate the charge) but the next time you create the stack it will have a new Elastic IP.

Finally, the Global Accelerator endpoint may give you better performance (in the form of lower latency) to connect to the hub. You should test using the Elastic IP and the Global Accelerator IPs. Use whichever one is lower.

## Configuration and how it works

At startup, `midihub.py` reads a configuration file called `midiports`. If not present, it assumes defaults which are displayed below.

For two remote participants you will need to have four UDP listeners. There will actually be eight UDP ports under the hood (because RTP MIDI uses two ports per connection) but you only need to define four of them. They are deployed as a pair - two listeners will be joined to each other as a send/receive pair; as will the second pair.

As an example (and these are the defaults) the four listeners are:

 - UDP port 5040 - part of the first pair
 - UDP port 5042 - part of the first pair
 - UDP port 5050 - part of the second pair
 - UDP port 5052 - part of the second pair

These are arbitrary port numbers but the RTP MIDI common practice is to start from port 5000 and up.

MidiHub will start four `rtpmidi` daemons to listen on those parts. Then it will use `aconnect` (part of the ALSA package) to join them together as follows:

 - Port 5040 - MIDI In sending to port 5042
 - Port 5042 - MIDI Out receiving from port 5040
 - Port 5050 - MIDI In sending to port 5052
 - Port 5052 - MIDI Out receiving from port 5050

Remote participant A will connect their MIDI Out port to port 5040. Remote participant B will connect their MIDI In port to port 5042. Anything sent to port 5040 by participant A will be sent to participant B who is listening on port 5042.

At the same time, participant B will connect their MIDI Out port to port 5050; participant A will connect their MIDI in port to port 5052.

Using this configuration, the two participants are linked together as if they had two (virtual) MIDI cables connecting them together.

To configure this in the `midiports` file you'd use a small piece of JSON:
```
{"GroupOne": [5040, 5042], "GroupTwo": [5050, 5052]}
```

Here, `GroupOne` and `GroupTwo` are names for each pair of ports. The names are not important but they must be unique.

If you had a third participant who wished to going in then you might do this in the configuration file:
```
{"GroupOne": [5040, 5042], "GroupTwo": [5050, 5052], "GroupThree":[5060, 5062]}
```

This will create a third listener pair on ports 5060 and 5062. Participant C will connect their MIDI Out to port 5060. Participants A and B will connect their MIDI In to port 5062. Participant C will connect their MIDI In to ports 5042 and 5052. Now everyone is conencted together.

## Deploy manually (in AWS or not)

You might want to run this on your own (non-AWS) virtual machine. In AWS this runs on Ubuntu 22.04 so the package list below is based on that.

You'll need the following packages installed:

 - alsa-base
 - avahi-utils
 - linux-modules-6.2.0-1013-lowlatency - one of the prerequisites is the dummy sound card; it is this package in Ubuntu which installs it
 - python3
 - boto3 and alsa-midi (use pip3 to install)

Install the Python `boto3` library (`sudo pip3 install boto3`) - this is used in AWS to determine which region the software is running in; outside of AWS it doesn't matter but it is included so installing boto3 will avoid any errors.

Download and install `rtpmidi` by running `sudo dpkg -i rtpmidi_1.1.2-ubuntu22.04_amd64.deb`.

Download `midihub.py` and put it somewhere that you can run it. This is easiest done by cloning this repo. In AWS this is triggered every minute by cron - it automatically detects if it is still running and self-terminates if so. The running version starts `rtpmidid` and uses `aconnect` to join the MIDI sessions together. Options for where to find binaries are in `midihub.py` at the top of the file.

It's up to you whether you display the statistics or not. The `update-latency.py` and `update-participants.py` scripts can help here. They put the data into DynamoDb - you can use a different database if you like.
